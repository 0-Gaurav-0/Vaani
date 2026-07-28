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
    c._assistant_codex(1, audio, "fix the flaky test")
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
    c._assistant_codex(1, audio, "fix the flaky test", confirmed=True)
    assert runs == ["fix the flaky test"]
    assert h.rows and h.rows[-1]["final_text"] == "done"
