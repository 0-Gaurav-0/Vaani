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

        def run_skill(self, body, utterance, *, mcps=(), skill_id=None):
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


def test_matched_skill_uses_async_handoff(monkeypatch):
    class Job:
        session_id = None
        _thread = SimpleNamespace(is_alive=lambda: False)

    class Runner:
        def __init__(self):
            self.calls = []

        def run(self, prompt):
            self.calls.append(("run", prompt))
            return SimpleNamespace(stdout="chat", cancelled=False, timed_out=False)

        def start_handoff(self, prompt, *, on_accepted=None, on_complete=None, skill_id=None, timeout=None, **_kw):
            self.calls.append(("handoff", prompt, skill_id))
            if on_accepted:
                on_accepted("sess_skill")
            if on_complete:
                on_complete(
                    SimpleNamespace(
                        stdout="skill-done",
                        stderr="",
                        returncode=0,
                        cancelled=False,
                        timed_out=False,
                        session_id="sess_skill",
                    )
                )
            return Job()

        def run_skill(self, *a, **k):
            raise AssertionError("blocking run_skill must not be used")

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
    monkeypatch.setattr("vaani.hermes_notify.notify_handoff", lambda **kw: None)

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

    assert runner.calls and runner.calls[0][0] == "handoff"
    assert runner.calls[0][2] == "sample-skill"
    assert "Do it." in runner.calls[0][1]
    assert "run the sample skill for acme" in runner.calls[0][1]
    assert "[source: vaani]" in runner.calls[0][1]
    assert h.rows[0]["cleanup_status"] == "agent_handoff"
    assert h.rows[0]["raw_text"] == "run the sample skill for acme"
    assert "Handed to Hermes" in h.rows[0]["final_text"]


def test_no_skill_match_falls_back_to_codex(monkeypatch):
    class Job:
        session_id = None
        _thread = SimpleNamespace(is_alive=lambda: False)

    class Runner:
        def __init__(self):
            self.calls = []

        def run(self, prompt):
            self.calls.append(("run", prompt))
            return SimpleNamespace(stdout="chat-answer", cancelled=False, timed_out=False)

        def start_handoff(self, prompt, *, on_accepted=None, on_complete=None, skill_id=None, timeout=None, **_kw):
            self.calls.append(("handoff", prompt, skill_id))
            if on_accepted:
                on_accepted(None)
            if on_complete:
                on_complete(
                    SimpleNamespace(
                        stdout="chat-answer",
                        stderr="",
                        returncode=0,
                        cancelled=False,
                        timed_out=False,
                        session_id=None,
                    )
                )
            return Job()

        def run_skill(self, *a, **k):
            raise AssertionError("should not run skill")

    monkeypatch.setattr("vaani.controller.load_skill_index", lambda: [])
    monkeypatch.setattr("vaani.controller.match_skill", lambda utterance, skills: None)
    monkeypatch.setattr("vaani.controller.resolve_app", lambda text: None)
    monkeypatch.setattr("vaani.controller.resolve_youtube", lambda text: None)
    monkeypatch.setattr("vaani.controller.resolve_site", lambda text: None)
    monkeypatch.setattr("vaani.hermes_notify.notify_handoff", lambda **kw: None)

    runner = Runner()
    h = History()
    w = Window()
    c = Controller(
        recorder=Rec(),
        groq=Groq("ask Vaani what are my assigned todos"),
        delivery=Delivery(),
        history=h,
        codex=runner,
        result_window=w,
        key_provider=lambda: "key",
    )
    assert c.trigger_assistant()
    assert c.stop()
    c._worker.join(1)
    assert runner.calls and runner.calls[0][0] == "handoff"
    assert "what are my assigned todos" in runner.calls[0][1]
    assert "[source: vaani]" in runner.calls[0][1]
    assert h.rows[0]["cleanup_status"] == "agent_handoff"


def test_mutation_request_never_starts_handoff(monkeypatch):
    class Runner:
        def start_handoff(self, *a, **k):
            raise AssertionError("mutation must not hand off")

        def run(self, *a, **k):
            raise AssertionError("mutation must not run")

    monkeypatch.setattr("vaani.controller.load_skill_index", lambda: [])
    monkeypatch.setattr("vaani.controller.match_skill", lambda utterance, skills: None)
    monkeypatch.setattr("vaani.controller.resolve_app", lambda text: None)
    monkeypatch.setattr("vaani.controller.resolve_youtube", lambda text: None)
    monkeypatch.setattr("vaani.controller.resolve_site", lambda text: None)

    h = History()
    c = Controller(
        recorder=Rec(),
        groq=Groq("refactor the flaky login test"),
        delivery=Delivery(),
        history=h,
        codex=Runner(),
        result_window=Window(),
        key_provider=lambda: "key",
    )
    assert c.trigger_assistant()
    assert c.stop()
    c._worker.join(1)
    assert h.rows and h.rows[0]["cleanup_status"] == "agent_read_only"


def test_ai_route_play_when_fast_path_misses(monkeypatch):
    from vaani.assistant_route import RouteDecision

    class RoutingGroq(Groq):
        def route(self, utterance, key, *, cancel=None):
            return RouteDecision(
                intent="play",
                query="spiderman brand new day trailer",
                target="youtube",
                confidence=0.92,
            )

    class Browser:
        def __init__(self):
            self.urls = []

        def open(self, url, *, prefer=None):
            self.urls.append(url)
            return "Opened browser."

    monkeypatch.setattr("vaani.controller.resolve_app", lambda text: None)
    monkeypatch.setattr("vaani.controller.resolve_youtube", lambda text: None)
    monkeypatch.setattr("vaani.controller.resolve_site", lambda text: None)
    monkeypatch.setattr(
        "vaani.controller.resolve_play_target",
        lambda query, target="youtube": SimpleNamespace(
            name=f"YouTube: {query}",
            url="https://www.youtube.com/watch?v=abcdefghijk",
            browser=None,
        ),
    )

    browser = Browser()
    h = History()
    c = Controller(
        recorder=Rec(),
        groq=RoutingGroq("play brand new day trailer on youtube"),
        delivery=Delivery(),
        history=h,
        key_provider=lambda: "key",
        browser_launcher=browser,
    )
    assert c.trigger_assistant()
    assert c.stop()
    c._worker.join(1)
    assert browser.urls == ["https://www.youtube.com/watch?v=abcdefghijk"]
    assert h.rows[0]["cleanup_status"] == "browser_action"


def test_ai_route_open_site_and_app(monkeypatch):
    from vaani.assistant_route import RouteDecision
    from vaani.apps import AppTarget

    class RoutingGroq(Groq):
        def __init__(self, text, decision):
            super().__init__(text)
            self._decision = decision

        def route(self, utterance, key, *, cancel=None):
            return self._decision

    class Browser:
        def __init__(self):
            self.urls = []

        def open(self, url, *, prefer=None):
            self.urls.append(url)
            return "Opened browser."

    monkeypatch.setattr("vaani.controller.resolve_app", lambda text: None)
    monkeypatch.setattr("vaani.controller.resolve_youtube", lambda text: None)
    monkeypatch.setattr("vaani.controller.resolve_site", lambda text: None)
    monkeypatch.setattr(
        "vaani.controller.resolve_browse_query",
        lambda query, browser=None, config_path=None: SimpleNamespace(
            name="GitHub", url="https://github.com/", browser=browser
        ),
    )

    browser = Browser()
    h = History()
    c = Controller(
        recorder=Rec(),
        groq=RoutingGroq(
            "github pe jao",
            RouteDecision(intent="open", query="github", target="site", confidence=0.9),
        ),
        delivery=Delivery(),
        history=h,
        key_provider=lambda: "key",
        browser_launcher=browser,
    )
    assert c.trigger_assistant()
    assert c.stop()
    c._worker.join(1)
    assert browser.urls == ["https://github.com/"]

    launched = []

    class AppLauncher:
        def resolve(self, text):
            return AppTarget("Cursor", ("cursor",))

        def launch(self, app):
            launched.append(app.name)
            return f"Opened {app.name}."

    monkeypatch.setattr(
        "vaani.controller.resolve_app_name",
        lambda name: AppTarget("Cursor", ("cursor",)),
    )
    h2 = History()
    c2 = Controller(
        recorder=Rec(),
        groq=RoutingGroq(
            "cursor kholo",
            RouteDecision(intent="open", query="cursor", target="app", confidence=0.95),
        ),
        delivery=Delivery(),
        history=h2,
        key_provider=lambda: "key",
        app_launcher=AppLauncher(),
    )
    assert c2.trigger_assistant()
    assert c2.stop()
    c2._worker.join(1)
    assert launched == ["Cursor"]
    assert h2.rows[0]["cleanup_status"] == "app_action"
