from types import SimpleNamespace
from pathlib import Path

from vaani.controller import Controller
from vaani.skills import SkillMeta
from vaani.types import AppState


class Rec:
    def start(self):
        return SimpleNamespace(path=Path("/tmp/a.wav"), duration_seconds=1)

    def stop(self):
        return SimpleNamespace(path=Path("/tmp/a.wav"), duration_seconds=1)

    def cleanup(self):
        pass


class Groq:
    def transcribe(self, *a, **k):
        return SimpleNamespace(text=self.text, language="en")

    def cleanup(self, text, *a, **k):
        return SimpleNamespace(text=text, used_fallback=False)

    def __init__(self, text="hello"):
        self.text = text


class Delivery:
    def deliver(self, text):
        return "paste_dispatched"


class History:
    def __init__(self):
        self.rows = []

    def insert(self, **kw):
        self.rows.append(kw)
        return 1


class Window:
    def __init__(self):
        self.results = []

    def show(self, result):
        self.results.append(getattr(result, "stdout", result))

    def show_text(self, text):
        self.results.append(text)


def test_youtube_beats_skill(monkeypatch):
    class Runner:
        def __init__(self):
            self.calls = []

        def run(self, prompt):
            self.calls.append(("run", prompt))
            return SimpleNamespace(stdout="nope", cancelled=False, timed_out=False)

        def run_skill(self, body, utterance, *, mcps=()):
            self.calls.append(("skill", utterance, mcps))
            return SimpleNamespace(stdout="skill", cancelled=False, timed_out=False)

    class Browser:
        def open(self, url, *, prefer=None):
            return "Opened browser."

    runner = Runner()
    h = History()
    w = Window()
    c = Controller(
        recorder=Rec(),
        groq=Groq("play rickroll on youtube"),
        delivery=Delivery(),
        history=h,
        codex=runner,
        result_window=w,
        key_provider=lambda: "key",
        browser_launcher=Browser(),
    )
    monkeypatch.setattr(
        "vaani.controller.resolve_youtube",
        lambda text: SimpleNamespace(
            name="Rickroll on YouTube",
            url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            browser=None,
        ),
    )
    monkeypatch.setattr("vaani.controller.resolve_app", lambda text: None)
    monkeypatch.setattr("vaani.controller.resolve_site", lambda text: None)
    assert c.trigger_assistant()
    assert c.stop()
    c._worker.join(1)
    assert runner.calls == []
    assert h.rows[0]["cleanup_status"] == "browser_action"
    assert c.state is AppState.IDLE


def test_matched_skill_uses_run_skill(monkeypatch):
    class Runner:
        def __init__(self):
            self.calls = []

        def run(self, prompt):
            self.calls.append(("run", prompt))
            return SimpleNamespace(stdout="chat", cancelled=False, timed_out=False)

        def run_skill(self, body, utterance, *, mcps=()):
            self.calls.append(("skill", body, utterance, tuple(mcps)))
            return SimpleNamespace(stdout="skill-done", cancelled=False, timed_out=False)

    skill = SkillMeta(
        id="sample-skill",
        name="sample-skill",
        description="demo",
        path=Path("/tmp/SKILL.md"),
        aliases=("sample",),
        mcps=("browseros",),
    )
    monkeypatch.setattr("vaani.controller.load_skill_index", lambda: [skill])
    monkeypatch.setattr(
        "vaani.controller.match_skill",
        lambda utterance, skills: skill,
    )
    monkeypatch.setattr(SkillMeta, "body", lambda self: "# Sample\nDo it.")

    runner = Runner()
    h = History()
    w = Window()
    c = Controller(
        recorder=Rec(),
        groq=Groq("run the sample skill for acme"),
        delivery=Delivery(),
        history=h,
        codex=runner,
        result_window=w,
        key_provider=lambda: "key",
    )
    monkeypatch.setattr("vaani.controller.resolve_app", lambda text: None)
    monkeypatch.setattr("vaani.controller.resolve_youtube", lambda text: None)
    monkeypatch.setattr("vaani.controller.resolve_site", lambda text: None)

    assert c.trigger_assistant()
    assert c.stop()
    c._worker.join(1)

    assert runner.calls and runner.calls[0][0] == "skill"
    assert runner.calls[0][3] == ("browseros",)
    assert h.rows[0]["cleanup_status"] == "skill_action"
    assert h.rows[0]["final_text"] == "skill-done"
    assert w.results == ["skill-done"]


def test_no_skill_match_falls_back_to_codex(monkeypatch):
    class Runner:
        def __init__(self):
            self.calls = []

        def run(self, prompt):
            self.calls.append(("run", prompt))
            return SimpleNamespace(stdout="chat-answer", cancelled=False, timed_out=False)

        def run_skill(self, *a, **k):
            raise AssertionError("should not run skill")

    monkeypatch.setattr("vaani.controller.load_skill_index", lambda: [])
    monkeypatch.setattr("vaani.controller.match_skill", lambda utterance, skills: None)
    monkeypatch.setattr("vaani.controller.resolve_app", lambda text: None)
    monkeypatch.setattr("vaani.controller.resolve_youtube", lambda text: None)
    monkeypatch.setattr("vaani.controller.resolve_site", lambda text: None)

    runner = Runner()
    h = History()
    w = Window()
    c = Controller(
        recorder=Rec(),
        groq=Groq("what is the capital of France"),
        delivery=Delivery(),
        history=h,
        codex=runner,
        result_window=w,
        key_provider=lambda: "key",
    )
    assert c.trigger_assistant()
    assert c.stop()
    c._worker.join(1)
    assert runner.calls == [("run", "what is the capital of France")]
    assert h.rows[0]["cleanup_status"] == "skipped"
