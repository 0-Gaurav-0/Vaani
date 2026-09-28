import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from vaani.controller import Controller
from vaani.jev import JevClient, JevDecision, JevError, decision_from_tool, jev_enabled, parse_completion


def _completion(message: dict, model: str = "m:free") -> dict:
    return {"model": model, "choices": [{"message": message}]}


def _tool(name: str, args: dict) -> dict:
    return _completion(
        {"tool_calls": [{"type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}
    )


def _client(handler, **kw) -> JevClient:
    return JevClient(api_key="sk-test", transport=httpx.MockTransport(handler), **kw)


def test_play_tool_maps_to_play_decision():
    d = parse_completion(_tool("play_media", {"query": "Arijit Singh sad songs", "platform": "youtube"}))
    assert (d.intent, d.query, d.target) == ("play", "Arijit Singh sad songs", "youtube")
    assert d.model == "m:free"


def test_unknown_platform_defaults_to_youtube():
    d = decision_from_tool("play_media", {"query": "lofi", "platform": "spotify"})
    assert d.target == "youtube"


@pytest.mark.parametrize(
    "name,args,expected",
    [
        ("open_app", {"name": "VS Code"}, ("open", "VS Code", "app")),
        ("open_website", {"query": "github.com"}, ("open", "github.com", "site")),
        ("open_folder", {"name": "Downloads"}, ("open", "Downloads", "")),
        ("media_control", {"action": "next"}, ("media", "next song", "next")),
        ("set_volume", {"action": "up"}, ("volume", "volume up", "up")),
        ("delegate_to_agent", {"task": "fix the failing test"}, ("codex", "fix the failing test", "")),
        ("type_text", {"text": "hello there"}, ("paste", "hello there", "")),
    ],
)
def test_tool_mapping(name, args, expected):
    d = decision_from_tool(name, args)
    assert (d.intent, d.query, d.target) == expected


def test_clarify_needs_two_valid_options():
    d = decision_from_tool(
        "ask_clarify",
        {
            "options": [
                {"label": "Play on YouTube", "action": "play_media", "query": "dhoom"},
                {"label": "Open Dhoom site", "action": "open_website", "query": "dhoom"},
                {"label": "bogus", "action": "rm -rf", "query": "x"},
            ]
        },
    )
    assert d.should_clarify
    assert [o.intent for o in d.options] == ["play", "open"]
    assert decision_from_tool("ask_clarify", {"options": [{"label": "a", "action": "answer", "query": "q"}]}) is None


def test_plain_content_is_answer_without_second_call():
    d = parse_completion(_completion({"content": "**Delhi** is the capital."}))
    assert d.intent == "qa"
    assert d.answer == "Delhi is the capital."


def test_json_tool_call_in_content_is_parsed():
    d = parse_completion(_completion({"content": '{"name": "open_app", "arguments": {"name": "Slack"}}'}))
    assert (d.intent, d.query) == ("open", "Slack")


def test_empty_or_unusable_raises():
    with pytest.raises(JevError):
        parse_completion(_completion({"content": "  "}))
    with pytest.raises(JevError):
        parse_completion(_tool("play_media", {"query": ""}))
    with pytest.raises(JevError):
        parse_completion({"choices": []})


def test_route_sends_tools_and_fallback_models():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_tool("open_app", {"name": "Terminal"}))

    d = _client(handler, models=("a:free", "b:free", "c:free", "d:free")).route("terminal kholo", context="earlier chat")
    assert d.intent == "open"
    assert seen["auth"] == "Bearer sk-test"
    assert seen["body"]["model"] == "a:free"
    assert seen["body"]["models"] == ["a:free", "b:free", "c:free"]  # OpenRouter max 3
    assert {t["function"]["name"] for t in seen["body"]["tools"]} >= {"play_media", "delegate_to_agent"}
    assert "earlier chat" in seen["body"]["messages"][0]["content"]


def test_upstream_429_raises_without_cooldown():
    c = _client(lambda r: httpx.Response(429, json={"error": {"message": "temporarily rate-limited upstream"}}))
    with pytest.raises(JevError) as exc:
        c.route("hi")
    assert exc.value.category == "quota"
    assert not c.in_cooldown


def test_daily_quota_429_starts_cooldown():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(429, json={"error": {"message": "Rate limit exceeded: free-models-per-day"}})

    c = _client(handler)
    with pytest.raises(JevError):
        c.route("hi")
    assert c.in_cooldown
    with pytest.raises(JevError):
        c.route("hi again")
    assert len(calls) == 1


def test_timeout_maps_to_jev_error():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(JevError) as exc:
        _client(handler).route("hi")
    assert exc.value.category == "timeout"


def test_missing_key_raises(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(JevError):
        JevClient(transport=httpx.MockTransport(lambda r: httpx.Response(200))).route("hi")


def test_jev_enabled_flags(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("VAANI_JEV", raising=False)
    assert not jev_enabled()
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    assert jev_enabled()
    monkeypatch.setenv("VAANI_JEV", "0")
    assert not jev_enabled()


# ---- controller integration -------------------------------------------------


class _Rec:
    def start(self):
        return SimpleNamespace(path=Path("/tmp/a.wav"), duration_seconds=1)

    def stop(self):
        return SimpleNamespace(path=Path("/tmp/a.wav"), duration_seconds=1)

    def cleanup(self):
        pass


class _Groq:
    def __init__(self, text):
        self.text = text
        self.routed = []
        self.answered = []

    def transcribe(self, *a, **k):
        return SimpleNamespace(text=self.text, language="en")

    def route(self, utterance, key, *, cancel=None):
        from vaani.assistant_route import RouteDecision

        self.routed.append(utterance)
        return RouteDecision(intent="qa", query=utterance, confidence=0.9)

    def answer(self, q, key, *, cancel=None, context=None):
        self.answered.append(q)
        return SimpleNamespace(text="groq answer")


class _History:
    def __init__(self):
        self.rows = []

    def insert(self, **kw):
        self.rows.append(kw)
        return 1


class _Feedback:
    def __init__(self):
        self.answers = []

    def play(self, cue):
        return True

    def show_answer(self, q, a):
        self.answers.append((q, a))


class _Jev:
    def __init__(self, decision=None, exc=None, answer_exc=None):
        self.decision, self.exc, self.calls = decision, exc, []
        self.answer_exc, self.answered = answer_exc, []

    def route(self, utterance, *, cancel=None, context=None):
        self.calls.append(utterance)
        if self.exc:
            raise self.exc
        return self.decision

    def answer(self, question, key=None, *, cancel=None, context=None):
        self.answered.append(question)
        if self.answer_exc:
            raise self.answer_exc
        return SimpleNamespace(text="jev answer")


def _run(controller):
    assert controller.trigger_assistant()
    assert controller.stop()
    controller._worker.join(2)


def _no_fast_paths(monkeypatch):
    for name in ("resolve_app", "resolve_youtube", "resolve_site", "resolve_folder"):
        monkeypatch.setattr(f"vaani.controller.{name}", lambda *a, **k: None)
    monkeypatch.setattr("vaani.controller.load_context", lambda: "")
    monkeypatch.setattr("vaani.controller.append_turn", lambda *a, **k: None)
    monkeypatch.setattr("vaani.controller.is_silent_wav", lambda p: False)


def test_controller_uses_jev_answer_without_groq(monkeypatch):
    _no_fast_paths(monkeypatch)
    groq, fb, h = _Groq("who won the 2011 world cup"), _Feedback(), _History()
    jev = _Jev(JevDecision("qa", "", "", 0.9, answer="India won it."))
    c = Controller(recorder=_Rec(), groq=groq, delivery=None, history=h, feedback=fb,
                   key_provider=lambda: "key", jev=jev)
    _run(c)
    assert jev.calls == ["who won the 2011 world cup"]
    assert groq.routed == [] and groq.answered == []
    assert fb.answers == [("who won the 2011 world cup", "India won it.")]
    assert h.rows[-1]["cleanup_status"] == "qa_answer"


def test_jev_failure_never_uses_groq_llm_by_default(monkeypatch):
    _no_fast_paths(monkeypatch)
    groq, fb, h = _Groq("what is the time in tokyo"), _Feedback(), _History()
    jev = _Jev(exc=JevError("quota"))
    c = Controller(recorder=_Rec(), groq=groq, delivery=None, history=h, feedback=fb,
                   key_provider=lambda: "key", jev=jev)
    _run(c)
    # Groq = STT only: heuristic says qa → answered by Jev (OpenRouter), not Groq.
    assert groq.routed == [] and groq.answered == []
    assert jev.answered == ["what is the time in tokyo"]
    assert fb.answers[-1][1] == "jev answer"


def test_jev_answer_failure_shows_unavailable_not_groq(monkeypatch):
    from vaani.controller import JEV_UNAVAILABLE

    _no_fast_paths(monkeypatch)
    groq, fb, h = _Groq("what is the time in tokyo"), _Feedback(), _History()
    jev = _Jev(exc=JevError("quota"), answer_exc=JevError("quota"))
    c = Controller(recorder=_Rec(), groq=groq, delivery=None, history=h, feedback=fb,
                   key_provider=lambda: "key", jev=jev)
    _run(c)
    assert groq.answered == []
    assert fb.answers[-1][1] == JEV_UNAVAILABLE


def test_opt_in_groq_fallback_router(monkeypatch):
    _no_fast_paths(monkeypatch)
    groq, fb, h = _Groq("what is the time in tokyo"), _Feedback(), _History()
    jev = _Jev(exc=JevError("quota"))
    c = Controller(recorder=_Rec(), groq=groq, delivery=None, history=h, feedback=fb,
                   key_provider=lambda: "key", jev=jev, jev_groq_fallback=True)
    _run(c)
    assert groq.routed == ["what is the time in tokyo"]
    # Answer still comes from Jev: Groq's LLM only ever routes in fallback mode.
    assert jev.answered == ["what is the time in tokyo"]


def test_jev_client_answer_uses_no_tools():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_completion({"content": "Canberra."}))

    out = _client(handler).answer("capital of australia", "ignored-groq-key", context="ctx")
    assert out.text == "Canberra."
    assert "tools" not in seen["body"]
    assert seen["body"]["messages"][0]["content"].startswith("You are Jev")
    assert "ctx" in seen["body"]["messages"][0]["content"]


def test_controller_jev_media_uses_deterministic_resolver(monkeypatch):
    _no_fast_paths(monkeypatch)
    ran = []
    monkeypatch.setattr("vaani.controller.run_media_action", lambda action, sender=None: ran.append(action.name) or "ok")
    groq, fb, h = _Groq("agla gaana lagao yaar"), _Feedback(), _History()
    jev = _Jev(JevDecision("media", "next song", "next", 0.9))
    c = Controller(recorder=_Rec(), groq=groq, delivery=None, history=h, feedback=fb,
                   key_provider=lambda: "key", jev=jev)
    # Raw phrase must miss the fast path so Jev is consulted.
    monkeypatch.setattr("vaani.controller.resolve_media_action",
                        lambda text: __import__("vaani.media", fromlist=["x"]).resolve_media_action(text)
                        if text == "next song" else None)
    _run(c)
    assert ran == ["next track"]
    assert h.rows[-1]["cleanup_status"] == "media_action"


def test_jev_first_skips_fast_paths(monkeypatch):
    _no_fast_paths(monkeypatch)
    monkeypatch.setattr("vaani.controller.resolve_volume_action", lambda text: (_ for _ in ()).throw(AssertionError("fast path ran")))
    groq, fb, h = _Groq("mute karo"), _Feedback(), _History()
    jev = _Jev(JevDecision("qa", "", "", 0.9, answer="ok"))
    c = Controller(recorder=_Rec(), groq=groq, delivery=None, history=h, feedback=fb,
                   key_provider=lambda: "key", jev=jev, jev_first=True)
    _run(c)
    assert jev.calls == ["mute karo"]


def test_jev_brief_cannot_launder_mutating_utterance(monkeypatch):
    _no_fast_paths(monkeypatch)
    started = []

    class Runner:
        def start_handoff(self, *a, **k):
            started.append(a)

        def run(self, prompt):
            started.append(prompt)

    groq, fb, h = _Groq("delete all my old emails"), _Feedback(), _History()
    jev = _Jev(JevDecision("codex", "Review the inbox and summarize old emails", "", 0.9))
    c = Controller(recorder=_Rec(), groq=groq, delivery=None, history=h, feedback=fb,
                   key_provider=lambda: "key", jev=jev, codex=Runner())
    _run(c)
    assert started == []
    assert h.rows[-1]["cleanup_status"] == "agent_read_only"


def test_disagreeing_stt_passes_go_to_jev_with_both(monkeypatch):
    from vaani.groq import TranscriptResult

    _no_fast_paths(monkeypatch)
    youtube = []
    monkeypatch.setattr("vaani.controller.resolve_youtube", lambda text: youtube.append(text))

    class HiGroq(_Groq):
        def transcribe_hinglish(self, *a, **k):
            return TranscriptResult("Play B.V", "en", ("Play B.V", "play beedi jalaaile"))

    seen = {}

    class CandJev(_Jev):
        def route(self, utterance, *, cancel=None, context=None, candidates=()):
            seen["candidates"] = candidates
            return JevDecision("qa", "", "", 0.9, answer="ok")

    groq, fb, h = HiGroq("unused"), _Feedback(), _History()
    c = Controller(recorder=_Rec(), groq=groq, delivery=None, history=h, feedback=fb,
                   key_provider=lambda: "key", jev=CandJev())
    _run(c)
    assert seen["candidates"] == ("Play B.V", "play beedi jalaaile")
    assert youtube == []  # the "b.v" fast path never ran
