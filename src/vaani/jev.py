"""Jev — Vaani's LLM assistant brain on OpenRouter (free models, tool calling).

Jev replaces the Groq JSON router for assistant utterances the deterministic
fast paths (open / play / mute / media keys) did not catch. One chat call with
tools returns either a tool call (mapped to a :class:`JevDecision`, which the
controller executes with the existing launchers) or a plain-text answer shown
in the pill — so Q&A costs one request, not route + answer.

Free-tier reality (measured 2026-09-28): 50 successful requests/day per key,
and individual free models are often rate-limited upstream (HTTP 429). We send
an OpenRouter ``models`` fallback list and keep a tight deadline; any failure
raises :class:`JevError` so the controller falls back to the Groq router.

Config (env / ``.env``):
  OPENROUTER_API_KEY   required; Jev is off without it
  VAANI_JEV=0          disable Jev entirely
  VAANI_JEV_FIRST=1    ask Jev before the deterministic fast paths
  VAANI_JEV_MODELS     comma-separated model ids (first = primary)
  VAANI_JEV_TIMEOUT    seconds (default 6)
  VAANI_JEV_GROQ_FALLBACK=1  if Jev fails, use Groq's LLM router (default off:
                       Groq is STT only; OpenRouter is Jev's only LLM)
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime
from threading import Event
from typing import Any, Callable

import httpx

from .assistant_route import RouteDecision, RouteOption

LOGGER = logging.getLogger("vaani")

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
# Order = preference. Only ling answered in the 2026-09-28 bench (1.2s, correct
# tool call); the rest were upstream-429 at that moment but are tool-capable.
# OpenRouter rejects a ``models`` list longer than 3 (HTTP 400).
MAX_FALLBACK_MODELS = 3
# Fallbacks must be *fast*: nemotron-3.5-lightning took >12s (2026-09-28) and
# nemotron-super 9s, which blows the deadline — worse than failing over to the
# heuristic. Gemma answers (or 429s) in <1s.
DEFAULT_MODELS: tuple[str, ...] = (
    "inclusionai/ling-3.0-flash-sante:free",
    "google/gemma-4-26b-a4b-it:free",
    "google/gemma-4-31b-it:free",
)
KEEPALIVE_S = 120.0
DEFAULT_TIMEOUT_S = 6.0
# After the daily free quota is gone, stop calling (each call would just 429).
DAILY_QUOTA_COOLDOWN_S = 60 * 60
MAX_ANSWER_CHARS = 600
MAX_CONTEXT_CHARS = 1500

PLATFORMS = ("youtube", "netflix", "prime", "hotstar", "sonyliv")
MEDIA_ACTIONS = ("pause", "resume", "next", "previous", "stop")
VOLUME_ACTIONS = ("mute", "unmute", "up", "down")

# Canonical phrases the existing deterministic resolvers already understand.
MEDIA_PHRASES = {
    "pause": "pause",
    "resume": "resume",
    "next": "next song",
    "previous": "previous song",
    "stop": "stop",
}
VOLUME_PHRASES = {
    "mute": "mute",
    "unmute": "unmute",
    "up": "volume up",
    "down": "volume down",
}


class JevError(RuntimeError):
    def __init__(self, category: str, message: str = "Jev request failed"):
        super().__init__(message)
        self.category = category


@dataclass(frozen=True)
class JevDecision(RouteDecision):
    """RouteDecision plus Jev extras.

    ``answer``: text to show for intent ``qa`` (already generated).
    Extra intents beyond the Groq router: ``media`` and ``volume`` — ``query``
    then holds a canonical phrase for the deterministic resolvers.
    """

    answer: str = ""
    model: str = ""
    # intent "type_in_app": text to write once the app is focused.
    text: str = ""


def _fn(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


_STR = {"type": "string"}

TOOLS: list[dict] = [
    _fn(
        "play_media",
        "Play a song, video, trailer or show. Use for play/watch/chalao/bajao/suno/lagao.",
        {
            "query": {**_STR, "description": "Search text in English/Latin keywords, e.g. 'Arijit Singh sad songs'"},
            "platform": {"type": "string", "enum": list(PLATFORMS), "description": "Default youtube"},
        },
        ["query"],
    ),
    _fn(
        "open_app",
        "Open a desktop application (editor, terminal, browser, Slack, Spotify, settings…).",
        {"name": {**_STR, "description": "App name, e.g. 'VS Code', 'Terminal'"}},
        ["name"],
    ),
    _fn(
        "open_website",
        "Open a website, web app, domain, or a Google search in the browser.",
        {"query": {**_STR, "description": "Site name, domain, or search text, e.g. 'gmail', 'github.com'"}},
        ["query"],
    ),
    _fn(
        "open_folder",
        "Open a local folder in the file manager (Downloads, Documents, Desktop, a project…).",
        {"name": _STR},
        ["name"],
    ),
    _fn(
        "open_project",
        "Open a code project / repo / folder in an editor or IDE (VS Code, Cursor, Zed, Antigravity).",
        {
            "project": {**_STR, "description": "Project or folder name as spoken, e.g. 'vaani', 'saleshandy brain'"},
            "editor": {
                "type": "string",
                "enum": ["code", "cursor", "zed", "antigravity", "default"],
                "description": "code = VS Code. Use default if the user named no editor.",
            },
        },
        ["project"],
    ),
    _fn(
        "open_app_and_type",
        "Open an app (text editor/notes, Obsidian, LibreOffice Writer…) and type the given text into it.",
        {
            "app": {**_STR, "description": "App name, e.g. 'text editor', 'obsidian'"},
            "text": {**_STR, "description": "Exact text to type, in the user's words"},
        },
        ["app", "text"],
    ),
    _fn(
        "computer_task",
        "Do a multi-step task INSIDE desktop apps by clicking and typing: e.g. in VS Code create a new "
        "file and write something, fill a field, use an app's menus, message someone in a chat app. "
        "Use this (not delegate_to_agent) whenever the task needs clicking inside an app's window.",
        {"goal": {**_STR, "description": "Self-contained goal in English, including exact text to type"}},
        ["goal"],
    ),
    _fn(
        "media_control",
        "Control whatever media is currently playing.",
        {"action": {"type": "string", "enum": list(MEDIA_ACTIONS)}},
        ["action"],
    ),
    _fn(
        "set_volume",
        "Change system volume.",
        {"action": {"type": "string", "enum": list(VOLUME_ACTIONS)}},
        ["action"],
    ),
    _fn(
        "delegate_to_agent",
        "Hand real multi-step work to the coding/research agent: writing or fixing code, "
        "research across websites, files, emails, anything needing tools beyond this list. "
        "Not for simple questions you can answer yourself.",
        {"task": {**_STR, "description": "Clear, self-contained task brief in English"}},
        ["task"],
    ),
    _fn(
        "type_text",
        "Type the given text into the focused app (user is dictating content, not asking).",
        {"text": _STR},
        ["text"],
    ),
    _fn(
        "ask_clarify",
        "Only when the request is genuinely ambiguous: offer 2-4 choices.",
        {
            "options": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "label": _STR,
                        "action": {
                            "type": "string",
                            "enum": ["play_media", "open_app", "open_website", "answer", "delegate_to_agent"],
                        },
                        "query": _STR,
                    },
                    "required": ["label", "action", "query"],
                },
            }
        },
        ["options"],
    ),
]

SYSTEM_PROMPT = (
    "You are Jev, the voice assistant inside Vaani on the user's Linux laptop. "
    "Input is a speech transcript: English, Hindi or Hinglish, may contain STT errors — "
    "infer the intended meaning. Decide in one step:\n"
    "- An action → call exactly ONE tool.\n"
    "- A question, chat, or advice → reply directly in plain text (no tool): at most 3 short "
    "sentences, no markdown, same language/style as the user (Hinglish in Latin script if "
    "they spoke Hinglish).\n"
    "Rules: prefer play_media for play/watch/chalao/bajao/suno. Prefer open_app for desktop "
    "apps, open_website for web services and domains. Use delegate_to_agent only for real "
    "multi-step work. Never invent an action for filler, noise or a single stray word — reply "
    "briefly instead. Do not explain which tool you chose.\n"
    "Examples (utterance → call):\n"
    "'arijit ka koi sad gaana bajao' → play_media(query='Arijit Singh sad songs')\n"
    "'pushpa 2 trailer dikhao' → play_media(query='Pushpa 2 trailer')\n"
    "'netflix pe stranger things lagao' → play_media(query='Stranger Things', platform='netflix')\n"
    "'vs code kholo' / 'open code' → open_app(name='VS Code')\n"
    "'terminal khol do' → open_app(name='Terminal')\n"
    "'gmail pe jao' → open_website(query='gmail')\n"
    "'stackoverflow dot com kholo' → open_website(query='stackoverflow.com')\n"
    "'downloads folder kholo' → open_folder(name='Downloads')\n"
    "'vaani wala project cursor mein khol do' → open_project(project='vaani', editor='cursor')\n"
    "'notepad kholo aur likho kal 5 baje call hai' → open_app_and_type(app='text editor', text='kal 5 baje call hai')\n"
    "'vs code mein new file bana ke usme hello likh do' → computer_task(goal='In VS Code, create a new file and type hello')\n"
    "'slack pe rahul ko hi bhej do' → computer_task(goal='In Slack, send the message \"hi\" to Rahul')\n"
    "'agla gaana' / 'skip karo' → media_control(action='next')\n"
    "'ruko' / 'band karo gaana' → media_control(action='pause')\n"
    "'awaaz badhao' → set_volume(action='up')\n"
    "'is repo me failing test fix karo' → delegate_to_agent(task='Fix the failing tests in the current repo')\n"
    "'kal ka weather kaisa rahega' → delegate_to_agent(task='Check tomorrow's weather forecast for the user's city')\n"
    "'python me list sort kaise karte hain' → reply: 'sorted(my_list) ya my_list.sort() use karo.'\n"
    "When given two speech-to-text passes of the same audio: the English pass turns Hindi words "
    "into English-sounding junk, the Hindi pass (romanized) garbles English words. Combine "
    "them into what was really said — song/movie titles are usually right in the Hindi pass. "
    "The English pass may even TRANSLATE the Hindi ('beedi jalaile' → 'I have a cigarette'). "
    "In the Hindi pass 'play' often appears garbled as 'le', 'ple', 'lo' or 'log'. If the "
    "Hindi pass reads like a song or film title, the user wants it played.\n"
    "E.g. English 'Play B.V' + Hindi 'play beedi jalaaile' → play_media(query='Beedi Jalaile'); "
    "English 'I have a cigarette' + Hindi 'log beedi jale le' → play_media(query='Beedi Jalaile'); "
    "English 'Play Chaiya' + Hindi 'le chal chaiyya chaiyya' → play_media(query='Chaiyya Chaiyya'). "
    "If both passes are noise, reply briefly that you didn't catch it."
)


ANSWER_PROMPT = (
    "You are Jev, the voice assistant inside Vaani on the user's Linux laptop. "
    "Answer the spoken question in at most 3 short sentences, no markdown, same "
    "language/style as the user (Hinglish in Latin script if they spoke Hinglish)."
)


@dataclass(frozen=True)
class JevAnswer:
    text: str
    used_fallback: bool = False


def jev_enabled() -> bool:
    flag = os.environ.get("VAANI_JEV", "").strip().lower()
    if flag in {"0", "false", "no", "off"}:
        return False
    return bool(os.environ.get("OPENROUTER_API_KEY", "").strip())


def jev_groq_fallback() -> bool:
    return os.environ.get("VAANI_JEV_GROQ_FALLBACK", "").strip().lower() in {"1", "true", "yes", "on"}


def jev_first() -> bool:
    return os.environ.get("VAANI_JEV_FIRST", "").strip().lower() in {"1", "true", "yes", "on"}


def _env_models() -> tuple[str, ...]:
    raw = os.environ.get("VAANI_JEV_MODELS", "").strip()
    if not raw:
        return DEFAULT_MODELS
    models = tuple(m.strip() for m in raw.split(",") if m.strip())
    return models or DEFAULT_MODELS


def _env_timeout() -> float:
    try:
        value = float(os.environ.get("VAANI_JEV_TIMEOUT", "") or DEFAULT_TIMEOUT_S)
    except ValueError:
        return DEFAULT_TIMEOUT_S
    return max(1.0, min(30.0, value))


def _clip(value: object, limit: int = 240) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit]


def _clean_answer(text: str) -> str:
    out = text.strip()
    # Some free models leak reasoning tags or markdown emphasis.
    if "</think>" in out:
        out = out.rsplit("</think>", 1)[1].strip()
    out = out.replace("**", "").replace("__", "")
    if len(out) > MAX_ANSWER_CHARS:
        out = out[: MAX_ANSWER_CHARS - 1].rstrip() + "…"
    return out


_OPTION_ACTIONS = {
    "play_media": ("play", "youtube"),
    "open_app": ("open", "app"),
    "open_website": ("open", "site"),
    "answer": ("qa", ""),
    "delegate_to_agent": ("codex", ""),
}


def decision_from_tool(name: str, args: dict, *, model: str = "") -> JevDecision | None:
    """Map one tool call to a decision; None when the call is unusable."""
    name = (name or "").strip()
    if not isinstance(args, dict):
        args = {}
    if name == "play_media":
        query = _clip(args.get("query"))
        if not query:
            return None
        platform = _clip(args.get("platform"), 16).casefold()
        return JevDecision("play", query, platform if platform in PLATFORMS else "youtube", 0.9, model=model)
    if name == "open_app":
        query = _clip(args.get("name"), 120)
        return JevDecision("open", query, "app", 0.9, model=model) if query else None
    if name == "open_website":
        query = _clip(args.get("query"))
        return JevDecision("open", query, "site", 0.9, model=model) if query else None
    if name == "open_folder":
        query = _clip(args.get("name"), 120)
        return JevDecision("open", query, "", 0.9, model=model) if query else None
    if name == "open_project":
        project = _clip(args.get("project"), 120)
        editor = _clip(args.get("editor"), 16).casefold()
        if not project:
            return None
        return JevDecision("project", project, "" if editor in {"", "default"} else editor, 0.9, model=model)
    if name == "open_app_and_type":
        app = _clip(args.get("app"), 120)
        text = args.get("text")
        text = text.strip() if isinstance(text, str) else ""
        if not app or not text:
            return None
        return JevDecision("type_in_app", app, "", 0.9, model=model, text=text[:4000])
    if name == "computer_task":
        goal = _clip(args.get("goal"), 600)
        return JevDecision("computer", goal, "", 0.9, model=model) if goal else None
    if name == "media_control":
        action = _clip(args.get("action"), 16).casefold()
        phrase = MEDIA_PHRASES.get(action)
        return JevDecision("media", phrase, action, 0.9, model=model) if phrase else None
    if name == "set_volume":
        action = _clip(args.get("action"), 16).casefold()
        phrase = VOLUME_PHRASES.get(action)
        return JevDecision("volume", phrase, action, 0.9, model=model) if phrase else None
    if name == "delegate_to_agent":
        task = _clip(args.get("task"), 2000)
        return JevDecision("codex", task, "", 0.9, model=model) if task else None
    if name == "type_text":
        text = args.get("text")
        text = text.strip() if isinstance(text, str) else ""
        return JevDecision("paste", text[:4000], "", 0.9, model=model) if text else None
    if name == "ask_clarify":
        raw_opts = args.get("options")
        options: list[RouteOption] = []
        for item in raw_opts if isinstance(raw_opts, list) else []:
            if not isinstance(item, dict):
                continue
            mapped = _OPTION_ACTIONS.get(_clip(item.get("action"), 32))
            label = _clip(item.get("label"), 120)
            query = _clip(item.get("query"))
            if mapped is None or not label:
                continue
            options.append(RouteOption(label=label, intent=mapped[0], query=query or label, target=mapped[1]))
        if len(options) < 2:
            return None
        return JevDecision("clarify", "", "", 0.3, tuple(options[:5]), model=model)
    return None


def _parse_args(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            value = json.loads(raw)
        except ValueError:
            return {}
        return value if isinstance(value, dict) else {}
    return {}


def _tool_call_from_text(text: str) -> tuple[str, dict] | None:
    """Some models print the tool call as JSON instead of using tool_calls."""
    body = text.strip()
    if body.startswith("```"):
        body = body.strip("`")
        body = body.split("\n", 1)[1] if "\n" in body else body
    if not body.startswith("{"):
        return None
    try:
        value = json.loads(body)
    except ValueError:
        return None
    if not isinstance(value, dict):
        return None
    name = value.get("name") or value.get("tool") or value.get("function")
    if not isinstance(name, str):
        return None
    return name, _parse_args(value.get("arguments") or value.get("parameters") or {})


def parse_completion(body: Any) -> JevDecision:
    """Turn an OpenRouter chat completion into a decision (raises JevError)."""
    try:
        choice = body["choices"][0]
        message = choice["message"]
    except (KeyError, IndexError, TypeError):
        raise JevError("malformed", "no choices")
    model = body.get("model") if isinstance(body.get("model"), str) else ""
    calls = message.get("tool_calls") or []
    if isinstance(calls, list):
        for call in calls:
            fn = call.get("function") if isinstance(call, dict) else None
            if not isinstance(fn, dict):
                continue
            decision = decision_from_tool(fn.get("name"), _parse_args(fn.get("arguments")), model=model)
            if decision is not None:
                return decision
        if calls:
            raise JevError("malformed", "unusable tool call")
    content = message.get("content")
    if isinstance(content, list):  # content parts
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    text = content if isinstance(content, str) else ""
    as_tool = _tool_call_from_text(text)
    if as_tool is not None:
        decision = decision_from_tool(as_tool[0], as_tool[1], model=model)
        if decision is not None:
            return decision
    answer = _clean_answer(text)
    if not answer:
        raise JevError("malformed", "empty reply")
    return JevDecision("qa", "", "", 0.9, answer=answer, model=model)


class JevClient:
    def __init__(
        self,
        *,
        api_key: str | Callable[[], str | None] | None = None,
        models: tuple[str, ...] | None = None,
        timeout: float | None = None,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        logger: logging.Logger = LOGGER,
    ):
        self._api_key = api_key
        self.models = tuple(models or _env_models())
        self.timeout = float(timeout if timeout is not None else _env_timeout())
        self._transport = transport
        self._clock = clock
        self._logger = logger
        self._skip_until = 0.0
        self._client = self._new_client()

    def _new_client(self) -> httpx.Client:
        return httpx.Client(
            base_url=OPENROUTER_BASE_URL,
            timeout=httpx.Timeout(self.timeout, connect=min(3.0, self.timeout)),
            transport=self._transport,
            # Default 5s idle expiry meant a fresh TLS handshake per request.
            limits=httpx.Limits(max_keepalive_connections=2, keepalive_expiry=KEEPALIVE_S),
        )

    def warm(self) -> None:
        """Open the pooled connection while the user is still speaking.

        ``GET /key`` is free (does not count against the daily request quota).
        """
        key = self._key()
        if not key:
            return
        started = self._clock()
        try:
            self._client.get("/key", headers={"Authorization": f"Bearer {key}"}, timeout=5.0)
        except Exception:
            pass
        self._logger.info("event=jev_warm elapsed=%.2f", self._clock() - started)

    def _key(self) -> str:
        key = self._api_key() if callable(self._api_key) else self._api_key
        key = key or os.environ.get("OPENROUTER_API_KEY", "")
        return key.strip()

    def close(self) -> None:
        self._client.close()

    def reset(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass
        self._client = self._new_client()

    @property
    def in_cooldown(self) -> bool:
        return self._clock() < self._skip_until

    def _messages(
        self, utterance: str, context: str | None, candidates: tuple[str, ...] = ()
    ) -> list[dict]:
        now = datetime.now().strftime("%A %d %B %Y, %H:%M")
        system = f"{SYSTEM_PROMPT}\nNow: {now}."
        if context:
            system += "\nEarlier today (for follow-ups):\n" + context[-MAX_CONTEXT_CHARS:]
        user = utterance
        if len(candidates) >= 2:
            user = (
                "Two speech-to-text passes of the same audio:\n"
                f"English pass: {candidates[0]}\n"
                f"Hindi pass (romanized): {candidates[1]}"
            )
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

    def _post(self, payload: dict, *, cancel: Event | None = None) -> dict:
        """One chat completion on OpenRouter; raises JevError on any failure."""
        key = self._key()
        if not key:
            raise JevError("key", "OPENROUTER_API_KEY missing")
        if self.in_cooldown:
            raise JevError("quota", "daily free quota cooldown")
        if cancel is not None and cancel.is_set():
            raise JevError("cancelled", "cancelled")
        payload = {
            "model": self.models[0],
            "models": list(self.models[:MAX_FALLBACK_MODELS]),
            "temperature": 0,
            "reasoning": {"enabled": False},
            **payload,
        }
        try:
            response = self._client.post(
                "/chat/completions",
                json=payload,
                headers={
                    "Authorization": f"Bearer {key}",
                    "X-Title": "Vaani",
                },
            )
        except httpx.TimeoutException:
            raise JevError("timeout", "OpenRouter timeout")
        except httpx.HTTPError as exc:
            raise JevError("network", type(exc).__name__)
        if cancel is not None and cancel.is_set():
            raise JevError("cancelled", "cancelled")
        if response.status_code >= 400:
            detail = response.text[:300].lower()
            if response.status_code == 429 and ("per-day" in detail or "per day" in detail or "daily" in detail):
                self._skip_until = self._clock() + DAILY_QUOTA_COOLDOWN_S
                self._logger.warning("event=jev_daily_quota cooldown_s=%s", DAILY_QUOTA_COOLDOWN_S)
            self._logger.warning(
                "event=jev_http status=%s detail=%r", response.status_code, response.text[:160]
            )
            raise JevError(
                "quota" if response.status_code in (402, 429) else "http",
                f"http {response.status_code}",
            )
        try:
            body = response.json()
        except ValueError:
            raise JevError("malformed", "invalid json")
        if not isinstance(body, dict) or body.get("error"):
            raise JevError("http", "error body")
        return body

    def route(
        self,
        utterance: str,
        *,
        cancel: Event | None = None,
        context: str | None = None,
        candidates: tuple[str, ...] = (),
    ) -> JevDecision:
        started = self._clock()
        body = self._post(
            {
                "messages": self._messages(utterance, context, candidates),
                "tools": TOOLS,
                "tool_choice": "auto",
                "max_tokens": 300,
            },
            cancel=cancel,
        )
        decision = parse_completion(body)
        self._logger.info(
            "event=jev_route_done intent=%s target=%s model=%s elapsed=%.2f",
            decision.intent,
            decision.target or "-",
            decision.model or "-",
            self._clock() - started,
        )
        return decision

    def answer(
        self,
        question: str,
        key: str | None = None,
        *,
        cancel: Event | None = None,
        context: str | None = None,
    ) -> JevAnswer:
        """Plain Q&A (no tools). ``key`` is ignored — kept for GroqClient.answer parity."""
        started = self._clock()
        messages = self._messages(question, context)
        messages[0]["content"] = ANSWER_PROMPT + messages[0]["content"].split(SYSTEM_PROMPT, 1)[-1]
        body = self._post({"messages": messages, "max_tokens": 300}, cancel=cancel)
        try:
            content = body["choices"][0]["message"].get("content")
        except (KeyError, IndexError, TypeError, AttributeError):
            raise JevError("malformed", "no choices")
        if isinstance(content, list):
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        text = _clean_answer(content if isinstance(content, str) else "")
        if not text:
            raise JevError("malformed", "empty reply")
        self._logger.info(
            "event=jev_answer_done chars=%s elapsed=%.2f", len(text), self._clock() - started
        )
        return JevAnswer(text)
