import threading
import time
from pathlib import Path
from types import SimpleNamespace

from vaani.controller import Controller
from vaani.indicator_protocol import write_command


class _Rec:
    def start(self):
        return SimpleNamespace(path=Path("/tmp/none.wav"), duration_seconds=1)

    def stop(self):
        return SimpleNamespace(path=Path("/tmp/none.wav"), duration_seconds=1)

    def cleanup(self):
        pass


class _Groq:
    def __init__(self, text):
        self.text = text

    def transcribe(self, *a, **k):
        return SimpleNamespace(text=self.text, language="en")


class _History:
    def __init__(self):
        self.rows = []

    def insert(self, **kw):
        self.rows.append(kw)


class _Feedback:
    def __init__(self):
        self.previews = []
        self.shown = threading.Event()

    def play(self, cue):
        return True

    def show_confirm(self, question, action, seconds):
        self.previews.append((question, action, seconds))
        self.shown.set()


class _Launcher:
    def __init__(self):
        self.launched = []

    def resolve(self, text):
        from vaani.apps import resolve_app

        return resolve_app(text)

    def launch(self, target):
        self.launched.append(target.name)
        return f"Opened {target.name}."


def _controller(tmp_path, monkeypatch, text, secs):
    monkeypatch.setattr("vaani.controller.is_silent_wav", lambda p: False)
    fb, launcher = _Feedback(), _Launcher()
    c = Controller(
        recorder=_Rec(), groq=_Groq(text), delivery=None, history=_History(), feedback=fb,
        key_provider=lambda: "k", app_launcher=launcher,
        amplitude_path=tmp_path / "amp", indicator_control_path=tmp_path / "ctl.json",
    )
    c.confirm_seconds = secs
    return c, fb, launcher


def test_preview_shows_action_then_runs(tmp_path, monkeypatch):
    c, fb, launcher = _controller(tmp_path, monkeypatch, "open calculator", 0.3)
    assert c.trigger_assistant() and c.stop()
    c._worker.join(3)
    assert fb.previews == [("open calculator", "Open Calculator", 0.3)]
    assert launcher.launched == ["Calculator"]


def test_cancel_click_on_preview_stops_the_action(tmp_path, monkeypatch):
    c, fb, launcher = _controller(tmp_path, monkeypatch, "open calculator", 3.0)
    assert c.trigger_assistant() and c.stop()
    assert fb.shown.wait(2)
    write_command(tmp_path / "ctl.json", "option_1")
    c._worker.join(4)
    assert launcher.launched == []


def test_do_it_now_skips_the_wait(tmp_path, monkeypatch):
    c, fb, launcher = _controller(tmp_path, monkeypatch, "open calculator", 5.0)
    assert c.trigger_assistant() and c.stop()
    assert fb.shown.wait(2)
    started = time.monotonic()
    write_command(tmp_path / "ctl.json", "option_0")
    c._worker.join(4)
    assert launcher.launched == ["Calculator"]
    assert time.monotonic() - started < 2.0


def test_escape_cancel_during_preview(tmp_path, monkeypatch):
    c, fb, launcher = _controller(tmp_path, monkeypatch, "open calculator", 3.0)
    assert c.trigger_assistant() and c.stop()
    assert fb.shown.wait(2)
    c.cancel()
    c._worker.join(4)
    assert launcher.launched == []


def test_zero_seconds_disables_preview(tmp_path, monkeypatch):
    c, fb, launcher = _controller(tmp_path, monkeypatch, "open calculator", 0)
    assert c.trigger_assistant() and c.stop()
    c._worker.join(3)
    assert fb.previews == [] and launcher.launched == ["Calculator"]
