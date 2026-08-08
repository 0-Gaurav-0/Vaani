from types import SimpleNamespace
from pathlib import Path

from vaani.controller import Controller
from vaani.types import AppState


class Rec:
    def start(self):
        return SimpleNamespace(path=Path("/tmp/a.wav"), duration_seconds=1)

    def stop(self):
        return SimpleNamespace(path=Path("/tmp/a.wav"), duration_seconds=1)

    def cleanup(self):
        pass


class History:
    def __init__(self):
        self.rows = []

    def insert(self, **kw):
        self.rows.append(kw)
        return 1

    def close(self):
        pass


def test_codex_asks_confirm_before_run():
    runs = []

    class Codex:
        def run(self, prompt):
            runs.append(prompt)
            return SimpleNamespace(stdout="done", cancelled=False, timed_out=False)

    class Feedback:
        def __init__(self):
            self.clarify = None

        def play(self, *_a, **_k):
            pass

        def show_clarify(self, question, options):
            self.clarify = (question, list(options))

    fb = Feedback()
    c = Controller(
        recorder=Rec(),
        groq=SimpleNamespace(transcribe=lambda *a, **k: SimpleNamespace(text="x")),
        delivery=SimpleNamespace(deliver=lambda *a, **k: "ok", cancel=lambda: None),
        history=History(),
        feedback=fb,
        key_provider=lambda: "key",
        codex=Codex(),
    )
    c._token = 1
    audio = SimpleNamespace(path=Path("/tmp/a.wav"), duration_seconds=1.0)
    c._assistant_codex(1, audio, "what are my assigned todos")
    assert runs == []
    assert fb.clarify is not None
    assert any("Confirm" in o for o in fb.clarify[1])


def test_codex_runs_after_confirm():
    runs = []

    class Codex:
        def run(self, prompt):
            runs.append(prompt)
            return SimpleNamespace(stdout="done", cancelled=False, timed_out=False)

    h = History()
    c = Controller(
        recorder=Rec(),
        groq=SimpleNamespace(),
        delivery=SimpleNamespace(deliver=lambda *a, **k: "ok", cancel=lambda: None),
        history=h,
        feedback=SimpleNamespace(play=lambda *a, **k: None),
        key_provider=lambda: "key",
        codex=Codex(),
    )
    audio = SimpleNamespace(path=Path("/tmp/a.wav"), duration_seconds=1.0)
    c._token = 1
    c.state = AppState.PROCESSING
    c._assistant_codex(1, audio, "what are my assigned todos", confirmed=True)
    assert len(runs) == 1
    assert "[source: vaani]" in runs[0]
    assert "what are my assigned todos" in runs[0]
    assert h.rows and h.rows[-1]["final_text"] == "done"


def test_codex_rejects_mutation_without_handoff():
    runs = []

    class Codex:
        def run(self, prompt):
            runs.append(prompt)
            return SimpleNamespace(stdout="done", cancelled=False, timed_out=False)

        def start_handoff(self, *a, **k):
            raise AssertionError("handoff must not start for mutations")

    h = History()
    shown = []
    c = Controller(
        recorder=Rec(),
        groq=SimpleNamespace(),
        delivery=SimpleNamespace(deliver=lambda *a, **k: "ok", cancel=lambda: None),
        history=h,
        feedback=SimpleNamespace(
            play=lambda *a, **k: None,
            show_answer=lambda q, a: shown.append((q, a)),
        ),
        key_provider=lambda: "key",
        codex=Codex(),
    )
    audio = SimpleNamespace(path=Path("/tmp/a.wav"), duration_seconds=1.0)
    c._token = 1
    c.state = AppState.PROCESSING
    c._assistant_codex(1, audio, "delete my Basecamp todo", confirmed=True)
    assert runs == []
    assert shown and "only read" in shown[0][1].casefold()
    assert h.rows and h.rows[-1]["cleanup_status"] == "agent_read_only"


def test_skill_prompt_with_write_docs_still_handoffs_for_read_ask():
    """Skill markdown mentions create/update — must not block a read request."""
    calls = []

    class Job:
        session_id = None
        _thread = SimpleNamespace(is_alive=lambda: False)

    class Codex:
        def start_handoff(self, prompt, *, on_accepted=None, on_complete=None, **k):
            calls.append(prompt)
            if on_accepted:
                on_accepted(None)
            return Job()

    h = History()
    c = Controller(
        recorder=Rec(),
        groq=SimpleNamespace(),
        delivery=SimpleNamespace(deliver=lambda *a, **k: "ok", cancel=lambda: None),
        history=h,
        feedback=SimpleNamespace(play=lambda *a, **k: None),
        key_provider=lambda: "key",
        codex=Codex(),
    )
    audio = SimpleNamespace(path=Path("/tmp/a.wav"), duration_seconds=1.0)
    c._token = 1
    c.state = AppState.PROCESSING
    skill_prompt = (
        "## Skill\nUse `basecamp todos create` and `update` and `delete`.\n\n"
        "## User request\n\nwhat are my assigned todos\n"
    )
    c._assistant_codex(
        1,
        audio,
        "what are my assigned todos",
        confirmed=True,
        prompt=skill_prompt,
        skill_id="basecamp",
    )
    assert calls and "what are my assigned todos" in calls[0]
    assert h.rows and h.rows[-1]["cleanup_status"] == "agent_handoff"
