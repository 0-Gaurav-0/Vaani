from types import SimpleNamespace
from vaani.feedback import Feedback
from vaani.indicator_protocol import read_answer, read_phase

_ALLOWED_ENV = {
    "PATH",
    "LANG",
    "LC_ALL",
    "DISPLAY",
    "DBUS_SESSION_BUS_ADDRESS",
    "XDG_RUNTIME_DIR",
    "XAUTHORITY",
    "HOME",
}


def test_paplay_uses_safe_environment(monkeypatch):
    seen = {}
    monkeypatch.setattr("vaani.platform.linux.feedback.Path.is_file", lambda self: True)

    def run(*a, **kw):
        seen.update(kw)
        return SimpleNamespace(returncode=0)

    assert Feedback(runner=run).play("failure")
    assert set(seen["env"]) <= _ALLOWED_ENV


def test_success_cue_is_silent(monkeypatch):
    calls = []

    def run(*a, **kw):
        calls.append(a)
        return SimpleNamespace(returncode=0)

    assert Feedback(runner=run, beeper=lambda: calls.append("beep")).play("success")
    assert calls == []


def test_beep_fallback(monkeypatch):
    monkeypatch.setattr("vaani.platform.linux.feedback.Path.is_file", lambda self: False)
    called = []

    def run(*_a, **_kw):
        return SimpleNamespace(returncode=1)

    assert Feedback(runner=run, beeper=lambda: called.append(1)).play("failure")
    assert called


def test_processing_does_not_dismiss_indicator(tmp_path, monkeypatch):
    procs = []

    def fake_popen(args, **_kw):
        procs.append(args)
        return SimpleNamespace(terminate=lambda: None, poll=lambda: None, pid=1)

    fb = Feedback(
        runner=lambda *_a, **_k: SimpleNamespace(returncode=0),
        beeper=lambda: None,
        popen=fake_popen,
        amplitude_path=tmp_path / "amp",
        control_path=tmp_path / "ctl.json",
        log_dir=tmp_path / "logs",
    )
    fb.play("start")
    assert fb.indicator is not None
    fb.play("processing")
    assert fb.indicator is not None


def test_show_answer_writes_payload_keeps_indicator(tmp_path):
    procs = []

    def fake_popen(args, **_kw):
        procs.append(args)
        return SimpleNamespace(terminate=lambda: None, poll=lambda: None, pid=1)

    fb = Feedback(
        runner=lambda *_a, **_k: SimpleNamespace(returncode=0),
        beeper=lambda: None,
        popen=fake_popen,
        amplitude_path=tmp_path / "amp",
        control_path=tmp_path / "ctl.json",
        log_dir=tmp_path / "logs",
    )
    fb.play("start")
    fb.play("processing")
    fb.show_answer("Who?", "42")
    assert fb.indicator is not None
    assert read_phase(fb.phase_path) == "answer"
    assert read_answer(fb.answer_path) == {
        "question": "Who?",
        "answer": "42",
        "options": [],
    }


def test_start_reuses_answer_indicator_and_flips_to_recording(tmp_path):
    def fake_popen(args, **_kw):
        return SimpleNamespace(terminate=lambda: None, poll=lambda: None, pid=1)

    fb = Feedback(
        runner=lambda *_a, **_k: SimpleNamespace(returncode=0),
        beeper=lambda: None,
        popen=fake_popen,
        amplitude_path=tmp_path / "amp",
        control_path=tmp_path / "ctl.json",
        log_dir=tmp_path / "logs",
    )
    fb.play("start")
    fb.show_answer("Who?", "42")
    assert read_phase(fb.phase_path) == "answer"
    first = fb.indicator
    fb.play("start")
    assert fb.indicator is first
    assert read_phase(fb.phase_path) == "recording"
