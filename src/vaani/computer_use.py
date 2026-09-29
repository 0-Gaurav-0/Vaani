"""Computer use: do multi-step tasks inside desktop apps (NVIDIA-hosted model).

Loop: observe the *active window* (AT-SPI element list; a screenshot of that
window only when the list is thin) → model picks actions via tool calls →
execute (AT-SPI click / XTest keys / clipboard paste) → repeat.

Safety rails (this mutates other apps):
  * max steps / wall time; cancel checked before every action
  * commit actions (send, submit, delete, Enter in a message box, closing
    windows…) always need an explicit click — never auto-run
  * model failure mid-task stops the task; nothing is retried blindly
  * only the target window is captured; the pill says a screenshot was used

Config: NVIDIA_API_KEY (required), VAANI_COMPUTER_USE=1 (off by default),
VAANI_CU_MODELS (comma list), VAANI_CU_MAX_STEPS, VAANI_CU_MAX_S.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import httpx

LOGGER = logging.getLogger("vaani")

NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"
# kimi-k3: 5/5 correct button coordinates on a Calculator screenshot (2026-09-29),
# 1–12s per call. nemotron omni: backup (often 503 on the free tier).
DEFAULT_MODELS = ("moonshotai/kimi-k3", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning")
STEP_TIMEOUT_S = 30.0
THIN_TREE = 8  # fewer usable elements than this → send a screenshot
MAX_LIST = 120

# Apps where Enter is a newline, not "send".
EDITOR_APPS = ("gedit", "text editor", "cursor", "code", "visual studio code", "libreoffice", "obsidian", "zed", "antigravity")
_COMMIT_RE = re.compile(
    r"\b(send|submit|post|publish|delete|remove|discard|trash|pay|buy|purchase|order|confirm|"
    r"sign\s*out|log\s*out|uninstall|reply|share|merge|deploy|approve|don'?t\s+save|close\s+without)\b",
    re.I,
)
_CLOSE_KEYS = {"alt+f4", "ctrl+w", "ctrl+q", "ctrl+shift+w"}


def computer_use_enabled() -> bool:
    flag = os.environ.get("VAANI_COMPUTER_USE", "").strip().lower() in {"1", "true", "yes", "on"}
    return flag and bool(os.environ.get("NVIDIA_API_KEY", "").strip())


def _fn(name: str, desc: str, props: dict, req: list[str]) -> dict:
    return {"type": "function", "function": {"name": name, "description": desc,
            "parameters": {"type": "object", "properties": props, "required": req}}}


_S = {"type": "string"}
_I = {"type": "integer"}
TOOLS = [
    _fn("click", "Click an element from the list by its [id].", {"id": _I}, ["id"]),
    _fn("click_xy", "Click at pixel x,y of the screenshot (only when the element is not in the list).",
        {"x": _I, "y": _I, "label": {**_S, "description": "What you are clicking, e.g. 'Send button'"}}, ["x", "y", "label"]),
    _fn("focus", "Put the keyboard focus in an element [id] (e.g. a text field).", {"id": _I}, ["id"]),
    _fn("type_text", "Type text at the current keyboard focus.", {"text": _S}, ["text"]),
    _fn("press_keys", "Press a key or chord: 'Return', 'Tab', 'Escape', 'ctrl+n', 'ctrl+s', 'ctrl+k'…",
        {"keys": _S}, ["keys"]),
    _fn("open_app", "Launch a desktop app by name and wait for its window.", {"name": _S}, ["name"]),
    _fn("wait", "Wait for the UI to update (max 3s).", {"seconds": {"type": "number"}}, ["seconds"]),
    _fn("done", "The goal is complete (or impossible). Summarise in one short sentence.", {"summary": _S}, ["summary"]),
    _fn("ask_user", "Stop and ask the user one short question (missing info, ambiguity).", {"question": _S}, ["question"]),
]

SYSTEM = (
    "You operate the user's Linux desktop to finish one goal. Each turn you get the active window's app, "
    "title, a list of its UI elements ([id] role 'name'), maybe a screenshot, and the steps done so far. "
    "Reply with tool calls only: 1–4 actions that can run in a row without looking again (e.g. ctrl+n, "
    "type_text, ctrl+s). After a click that changes the screen, stop and look again. Prefer keyboard "
    "shortcuts and element [id]s over click_xy. Type exactly the text the user asked for. Never do "
    "anything beyond the goal: no extra messages, no deleting, no purchases. When the goal is achieved "
    "call done. If you need information you don't have (which file, which person), call ask_user. "
    "Every step listed under 'Steps so far' already ran successfully — never repeat them; continue "
    "from where they left off (check the element names for the current state, e.g. a display value)."
)


@dataclass
class Snapshot:
    app: str = ""
    title: str = ""
    bbox: list[int] = field(default_factory=lambda: [0, 0, 0, 0])
    elements: list[dict] = field(default_factory=list)

    def element(self, idx: int) -> dict | None:
        for el in self.elements:
            if el.get("id") == idx:
                return el
        return None

    def is_editor(self) -> bool:
        blob = f"{self.app} {self.title}".casefold()
        return any(a in blob for a in EDITOR_APPS)

    def listing(self) -> str:
        lines = []
        for el in self.elements[:MAX_LIST]:
            flags = (" (focused)" if el.get("focused") else "") + (" (editable)" if el.get("editable") else "")
            lines.append(f"[{el['id']}] {el['role']} '{el.get('name', '')}'{flags}")
        return "\n".join(lines) or "(no accessible elements — use the screenshot)"


@dataclass(frozen=True)
class Action:
    name: str
    args: dict


class A11yHelper:
    """Talks to the /usr/bin/python3 AT-SPI helper over JSON lines."""

    def __init__(self, popen: Callable[..., Any] = subprocess.Popen):
        src = Path(__file__).resolve().parents[1]
        env = os.environ.copy()
        parts = [str(src)]
        for site in Path(sys.prefix).glob("lib/python*/site-packages"):
            parts.append(str(site))
        env["PYTHONPATH"] = os.pathsep.join(parts)
        exe = "/usr/bin/python3" if Path("/usr/bin/python3").exists() else sys.executable
        self.proc = popen(
            [exe, "-m", "vaani.platform.linux.a11y_helper"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env,
        )

    def call(self, cmd: str, **kw: Any) -> dict:
        if self.proc.poll() is not None:
            return {"ok": False, "error": "helper exited"}
        self.proc.stdin.write(json.dumps({"cmd": cmd, **kw}) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        try:
            return json.loads(line) if line else {"ok": False, "error": "no reply"}
        except ValueError:
            return {"ok": False, "error": "bad reply"}

    def snapshot(self) -> Snapshot:
        d = self.call("snapshot")
        if not d.get("ok"):
            return Snapshot()
        return Snapshot(d.get("app", ""), d.get("title", ""), d.get("bbox") or [0, 0, 0, 0], d.get("elements") or [])

    def close(self) -> None:
        try:
            self.proc.stdin.close()
            self.proc.terminate()
        except Exception:
            pass


class XInput:
    """Pointer clicks and key chords via XTest."""

    def __init__(self, display: Any | None = None):
        from Xlib import X, XK
        from Xlib.display import Display

        self.X, self.XK = X, XK
        self.d = display or Display()
        try:
            XK.load_keysym_group("xf86")
        except Exception:
            pass

    def click(self, x: int, y: int) -> None:
        from Xlib.ext import xtest

        xtest.fake_input(self.d, self.X.MotionNotify, x=int(x), y=int(y))
        self.d.sync()
        time.sleep(0.05)
        xtest.fake_input(self.d, self.X.ButtonPress, 1)
        xtest.fake_input(self.d, self.X.ButtonRelease, 1)
        self.d.sync()

    _ALIASES = {"ctrl": "Control_L", "control": "Control_L", "shift": "Shift_L", "alt": "Alt_L",
                "super": "Super_L", "enter": "Return", "return": "Return", "esc": "Escape",
                "escape": "Escape", "tab": "Tab", "backspace": "BackSpace", "delete": "Delete",
                "space": "space", "up": "Up", "down": "Down", "left": "Left", "right": "Right",
                "home": "Home", "end": "End", "pageup": "Prior", "pagedown": "Next"}

    def keys(self, chord: str) -> None:
        from Xlib.ext import xtest

        codes = []
        for part in [p.strip() for p in chord.replace(" ", "").split("+") if p.strip()]:
            name = self._ALIASES.get(part.casefold(), part if len(part) > 1 else part.lower())
            if re.fullmatch(r"f\d{1,2}", name, re.I):
                name = name.upper()
            code = self.d.keysym_to_keycode(self.XK.string_to_keysym(name))
            if not code:
                raise ValueError(f"unknown key {part!r}")
            codes.append(code)
        for c in codes:
            xtest.fake_input(self.d, self.X.KeyPress, c)
        for c in reversed(codes):
            xtest.fake_input(self.d, self.X.KeyRelease, c)
        self.d.sync()


class NimBrain:
    def __init__(self, *, models: tuple[str, ...] | None = None, transport: httpx.BaseTransport | None = None):
        raw = os.environ.get("VAANI_CU_MODELS", "").strip()
        self.models = models or (tuple(m.strip() for m in raw.split(",") if m.strip()) if raw else DEFAULT_MODELS)
        self._client = httpx.Client(base_url=NIM_BASE_URL, timeout=httpx.Timeout(STEP_TIMEOUT_S, connect=5.0),
                                    transport=transport)

    def next_actions(self, messages: list[dict]) -> list[Action]:
        key = os.environ.get("NVIDIA_API_KEY", "").strip()
        last = "no model"
        for model in self.models:
            started = time.monotonic()
            try:
                r = self._client.post("/chat/completions", headers={"Authorization": f"Bearer {key}"}, json={
                    "model": model, "messages": messages, "tools": TOOLS, "tool_choice": "auto",
                    "temperature": 0, "max_tokens": 700,
                })
            except httpx.HTTPError as exc:
                last = f"{model}: {type(exc).__name__}"
                continue
            if r.status_code != 200:
                last = f"{model}: HTTP {r.status_code}"
                continue
            msg = (r.json().get("choices") or [{}])[0].get("message") or {}
            actions = []
            for call in msg.get("tool_calls") or []:
                fn = call.get("function") or {}
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except ValueError:
                    args = {}
                if fn.get("name"):
                    actions.append(Action(fn["name"], args if isinstance(args, dict) else {}))
            LOGGER.info("event=cu_model model=%s actions=%s elapsed=%.1f", model, len(actions),
                        time.monotonic() - started)
            if actions:
                return actions
            text = (msg.get("content") or "").split("</think>")[-1].strip()
            if text:
                return [Action("done", {"summary": text[:200]})]
            last = f"{model}: empty reply"
        raise RuntimeError(last)

    def close(self) -> None:
        self._client.close()


def describe(action: Action, snap: Snapshot) -> str:
    a = action.args
    if action.name in {"click", "focus"}:
        el = snap.element(int(a.get("id", -1))) or {}
        what = el.get("name") or el.get("role") or f"element {a.get('id')}"
        return f"{'Click' if action.name == 'click' else 'Focus'} “{what}”"
    if action.name == "click_xy":
        return f"Click “{a.get('label') or 'point'}”"
    if action.name == "type_text":
        t = str(a.get("text", ""))
        return f"Type “{t if len(t) <= 40 else t[:37] + '…'}”"
    if action.name == "press_keys":
        return f"Press {a.get('keys')}"
    if action.name == "open_app":
        return f"Open {a.get('name')}"
    if action.name == "wait":
        return "Wait"
    return action.name


def is_commit(action: Action, snap: Snapshot) -> bool:
    """Would this action send / submit / destroy something? → needs a click."""
    a = action.args
    if action.name == "click":
        el = snap.element(int(a.get("id", -1))) or {}
        return bool(_COMMIT_RE.search(el.get("name") or ""))
    if action.name == "click_xy":
        return bool(_COMMIT_RE.search(str(a.get("label") or ""))) or not snap.is_editor()
    if action.name == "press_keys":
        chord = str(a.get("keys", "")).replace(" ", "").casefold()
        if chord in _CLOSE_KEYS:
            return True
        if chord.endswith(("return", "enter")):
            return "ctrl" in chord or not snap.is_editor()
    return False


@dataclass
class Hooks:
    status: Callable[[str], None]
    confirm: Callable[[str], bool]
    cancelled: Callable[[], bool]
    type_text: Callable[[str], bool]
    open_app: Callable[[str], str]


class ComputerTask:
    def __init__(self, goal: str, hooks: Hooks, *, brain: Any, helper: Any, xinput: Any,
                 max_steps: int | None = None, max_s: float | None = None, clock=time.monotonic):
        self.goal, self.hooks, self.brain, self.helper, self.x = goal, hooks, brain, helper, xinput
        self.max_steps = max_steps or int(os.environ.get("VAANI_CU_MAX_STEPS", "12"))
        self.max_s = max_s or float(os.environ.get("VAANI_CU_MAX_S", "120"))
        self.clock = clock
        self.history: list[str] = []
        self.used_screenshot = False
        # Only these apps may be captured: where the user started + apps this task opened.
        self.allowed_apps: set[str] = set()

    def _messages(self, snap: Snapshot, shot: dict | None) -> list[dict]:
        done = "\n".join(self.history[-10:]) or "(nothing yet)"
        text = (f"Goal: {self.goal}\nActive app: {snap.app}  Window: {snap.title}\n"
                f"Steps so far:\n{done}\n\nElements:\n{snap.listing()}")
        if shot:
            text += f"\n\nScreenshot is {int(snap.bbox[2] * shot['scale'])}x{int(snap.bbox[3] * shot['scale'])} px of this window."
            content = [{"type": "text", "text": text},
                       {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{shot['png_b64']}"}}]
        else:
            content = text
        return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": content}]

    def _execute(self, action: Action, snap: Snapshot, shot: dict | None) -> str:
        a = action.args
        if action.name in {"click", "focus"}:
            r = self.helper.call(action.name, id=int(a.get("id", -1)))
            if r.get("ok") and r.get("xy"):
                self.x.click(*r["xy"])
            return "ok" if r.get("ok") else f"failed ({r.get('error')})"
        if action.name == "click_xy":
            if not shot:
                return "failed (no screenshot to map coordinates)"
            s = shot["scale"] or 1.0
            bx, by = shot["bbox"][0], shot["bbox"][1]
            self.x.click(bx + int(float(a.get("x", 0)) / s), by + int(float(a.get("y", 0)) / s))
            return "ok"
        if action.name == "type_text":
            return "ok" if self.hooks.type_text(str(a.get("text", ""))) else "failed"
        if action.name == "press_keys":
            self.x.keys(str(a.get("keys", "")))
            return "ok"
        if action.name == "open_app":
            return self.hooks.open_app(str(a.get("name", "")))
        if action.name == "wait":
            time.sleep(max(0.2, min(3.0, float(a.get("seconds", 1) or 1))))
            return "ok"
        return "unknown action"

    def run(self) -> str:
        started = self.clock()
        for step in range(1, self.max_steps + 1):
            if self.hooks.cancelled():
                return "Stopped."
            if self.clock() - started > self.max_s:
                return f"Stopped: took longer than {int(self.max_s)}s."
            snap = self.helper.snapshot()
            if step == 1:
                self.allowed_apps.add(snap.app or snap.title)
            usable = [e for e in snap.elements if e.get("name") or e.get("editable")]
            shot = None
            may_capture = (snap.app or snap.title) in self.allowed_apps
            if not may_capture:
                LOGGER.info("event=cu_screenshot_blocked app=%r", snap.app or snap.title)
            if may_capture and (len(usable) < THIN_TREE or (self.history and "failed" in self.history[-1])):
                shot = self.helper.call("screenshot", max=1280)
                shot = shot if shot.get("ok") else None
                if shot:
                    self.used_screenshot = True
            self.hooks.status(f"Step {step} · looking at {snap.app or 'the screen'}"
                              + (" (screenshot)" if shot else ""))
            try:
                actions = self.brain.next_actions(self._messages(snap, shot))
            except Exception as exc:
                LOGGER.warning("event=cu_model_failed detail=%s", exc)
                last = self.history[-1].split(". ", 1)[-1] if self.history else "nothing done"
                return f"Stopped at step {step}: model unavailable. Last: {last}"
            for action in actions[:4]:
                if self.hooks.cancelled():
                    return "Stopped."
                if action.name == "done":
                    return str(action.args.get("summary") or "Done.")[:200]
                if action.name == "ask_user":
                    return str(action.args.get("question") or "I need more information.")[:200]
                desc = describe(action, snap)
                if is_commit(action, snap):
                    LOGGER.info("event=cu_commit_gate action=%r", desc)
                    if not self.hooks.confirm(f"{desc} in {snap.app or 'this app'}"):
                        return f"Cancelled before: {desc}."
                self.hooks.status(f"Step {step} · {desc}")
                try:
                    result = self._execute(action, snap, shot)
                except Exception as exc:
                    result = f"failed ({type(exc).__name__})"
                self.history.append(f"{step}. {desc} → {result}")
                LOGGER.info("event=cu_step step=%s action=%r result=%s", step, desc, result)
                # Clicks by [id] act on a specific element, so a batch the model
                # planned from one look can continue. Coordinates / new windows
                # / failures mean the screen may have moved: look again.
                if action.name == "open_app" and result == "ok":
                    time.sleep(0.4)
                    opened = self.helper.snapshot()
                    self.allowed_apps.add(opened.app or opened.title)
                if action.name in {"click_xy", "open_app"} or result != "ok":
                    break
                time.sleep(0.15)
            time.sleep(0.35)
        return f"Stopped: reached {self.max_steps} steps."
