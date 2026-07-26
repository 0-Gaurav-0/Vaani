"""Controller wiring: interrogatives refuse before confirm / handler (T2.5)."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from vaani.controller import Controller
from vaani.intent.schema import Status
from vaani.types import AppState


class Rec:
    def start(self):
        return SimpleNamespace(path=Path("/tmp/a.wav"), duration_seconds=1)

    def stop(self):
        return SimpleNamespace(path=Path("/tmp/a.wav"), duration_seconds=1)

    def cleanup(self):
        pass


class Groq:
    def __init__(self, text: str):
        self.text = text

    def transcribe(self, *a, **k):
        return SimpleNamespace(text=self.text, language="en")

    def cleanup(self, text, *a, **k):
        return SimpleNamespace(text=text, used_fallback=False)

    def close(self):
        pass


class Delivery:
    def deliver(self, text, snapshot=None):
        return "paste_dispatched"

    def cancel(self):
        pass


class History:
    def __init__(self):
        self.rows = []

    def insert(self, **kw):
        self.rows.append(kw)
        return 1

    def close(self):
        pass


class Window:
    def __init__(self):
        self.results = []

    def show_text(self, text):
        self.results.append(text)


def _controller(tmp_path: Path, *, text: str):
    control = tmp_path / "indicator_control.json"
    amp = tmp_path / "amplitude"
    h = History()
    w = Window()
    c = Controller(
        recorder=Rec(),
        groq=Groq(text),
        delivery=Delivery(),
        history=h,
        result_window=w,
        key_provider=lambda: "key",
        amplitude_path=amp,
        indicator_control_path=control,
        max_duration=60,
    )
    return c, h, w


def test_interrogative_refuses_without_handler_or_confirm(tmp_path: Path):
    c, h, w = _controller(tmp_path, text="how do I free port 3000")
    verb = c.registry.get("system.port.free")
    assert verb is not None
    calls: list[str] = []
    original = verb.handler

    def spy(intent, context):
        calls.append(intent.verb)
        return original(intent, context)

    object.__setattr__(verb, "handler", spy)

    assert c.trigger_assistant()
    assert c.stop()
    c._worker.join(2)

    assert c.state is AppState.IDLE
    assert c.confirm.peek() is None
    assert calls == []
    assert h.rows
    final = h.rows[0]["final_text"].casefold()
    assert "say it as a command" in final or "free port 3000" in final
    assert w.results
    assert any("say it as a command" in t.casefold() for t in w.results)


def test_imperative_still_stages_confirm(tmp_path: Path):
    c, h, _w = _controller(tmp_path, text="free port 3000")
    verb = c.registry.get("system.port.free")
    assert verb is not None
    calls: list[str] = []

    def spy(intent, context):
        calls.append(intent.verb)
        from vaani.intent.schema import Result

        return Result(status=Status.OK, summary="ok", rung=2)

    object.__setattr__(verb, "handler", spy)

    assert c.trigger_assistant()
    assert c.stop()
    c._worker.join(2)

    pending = c.confirm.peek()
    assert pending is not None
    assert pending.verb == "system.port.free"
    assert calls == []  # staged, not executed
    assert not h.rows
