"""Unit tests for Linux platform adapters (mocked; safe on macOS/Windows CI)."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from vaani.indicator_protocol import read_phase
from vaani.platform.linux.feedback import LinuxFeedback
from vaani.platform.protocol import PlatformId


def test_linux_feedback_keeps_pill_on_processing(tmp_path: Path):
    calls: list[list[str]] = []

    def fake_popen(args, **_kwargs):
        calls.append(list(args))
        return SimpleNamespace(terminate=lambda: None, poll=lambda: None, pid=4242)

    fb = LinuxFeedback(
        runner=lambda *_a, **_k: SimpleNamespace(returncode=0),
        beeper=lambda: None,
        popen=fake_popen,
        amplitude_path=tmp_path / "amplitude",
        control_path=tmp_path / "control.json",
        log_dir=tmp_path / "logs",
    )
    assert fb.play("start")
    assert calls and calls[0][:3] == [
        sys.executable,
        "-m",
        "vaani.platform.linux.indicator_app",
    ]
    assert read_phase(fb.phase_path) == "recording"
    # Processing must NOT dismiss the pill — phase switch only.
    fb.play("processing")
    assert fb.indicator is not None
    assert read_phase(fb.phase_path) == "processing"
    fb.play("success")
    # Persistent pill: hidden (idle phase), process kept warm for next press.
    assert fb.indicator is not None
    assert read_phase(fb.phase_path) == "idle"
    fb.play("start")
    assert len(calls) == 1
    assert read_phase(fb.phase_path) == "recording"
    fb.shutdown()
    assert fb.indicator is None


def test_linux_feedback_non_persistent_kills_pill(tmp_path: Path):
    fb = LinuxFeedback(
        runner=lambda *_a, **_k: SimpleNamespace(returncode=0),
        beeper=lambda: None,
        popen=lambda *_a, **_k: SimpleNamespace(terminate=lambda: None, poll=lambda: None, pid=0),
        amplitude_path=tmp_path / "amplitude",
        control_path=tmp_path / "control.json",
        log_dir=tmp_path / "logs",
        persistent_indicator=False,
    )
    fb.play("start")
    fb.play("success")
    assert fb.indicator is None


def test_linux_feedback_prewarm_spawns_hidden_once(tmp_path: Path):
    calls: list = []

    def fake_popen(args, **_kwargs):
        calls.append(args)
        return SimpleNamespace(terminate=lambda: None, poll=lambda: None, pid=0)

    fb = LinuxFeedback(
        runner=lambda *_a, **_k: SimpleNamespace(returncode=0),
        beeper=lambda: None,
        popen=fake_popen,
        amplitude_path=tmp_path / "amplitude",
        control_path=tmp_path / "control.json",
        log_dir=tmp_path / "logs",
    )
    fb.prewarm()
    fb.prewarm()
    assert len(calls) == 1
    assert read_phase(fb.phase_path) == "idle"
    fb.play("start")
    assert len(calls) == 1
    assert read_phase(fb.phase_path) == "recording"


def test_linux_feedback_default_runner_plays_sound_async(tmp_path: Path, monkeypatch):
    import threading
    import subprocess

    gate = threading.Event()
    started: list = []

    def slow_run(*_a, **_k):
        started.append(1)
        gate.wait(2)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("vaani.platform.linux.feedback.Path.is_file", lambda self: True)
    fb = LinuxFeedback(
        beeper=lambda: None,
        popen=lambda *_a, **_k: SimpleNamespace(terminate=lambda: None, poll=lambda: None, pid=0),
        amplitude_path=tmp_path / "amplitude",
        control_path=tmp_path / "control.json",
        log_dir=tmp_path / "logs",
        sound_async=True,
    )
    fb.runner = slow_run
    assert fb.play("processing")  # returns before paplay finishes
    gate.set()
    assert fb.sound_async is True
    assert LinuxFeedback(runner=subprocess.run).sound_async is True


def test_linux_feedback_paplay_env_is_minimal(tmp_path: Path, monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(
        "vaani.platform.linux.feedback.Path.is_file", lambda self: True
    )

    def run(*_a, **kw):
        seen.update(kw)
        return SimpleNamespace(returncode=0)

    fb = LinuxFeedback(
        runner=run,
        beeper=None,
        amplitude_path=tmp_path / "amplitude",
        control_path=tmp_path / "control.json",
    )
    assert fb.play("success")
    assert set(seen["env"]) <= {"PATH", "LANG", "LC_ALL"}


def test_build_linux_bundle_id(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from vaani.config import Settings
    from vaani.platform.linux.runtime import build_linux

    settings = Settings.from_home(tmp_path, platform="linux")
    bundle = build_linux(settings)
    assert bundle.id is PlatformId.LINUX
    assert "amplitude" in str(bundle.settings.amplitude_path)
    assert hasattr(bundle.feedback, "play")


def test_audio_recorder_exposes_level(tmp_path: Path):
    from vaani.audio import AudioRecorderImpl

    rec = AudioRecorderImpl(tmp_path / "audio", amplitude_path=tmp_path / "amp")
    assert rec.level == 0.0
