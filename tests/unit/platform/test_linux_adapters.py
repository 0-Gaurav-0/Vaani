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
    assert fb.indicator is None


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
    assert bundle.system is not None
    assert hasattr(bundle.system, "volume_set")


def test_audio_recorder_exposes_level(tmp_path: Path):
    from vaani.audio import AudioRecorderImpl

    rec = AudioRecorderImpl(tmp_path / "audio", amplitude_path=tmp_path / "amp")
    assert rec.level == 0.0
