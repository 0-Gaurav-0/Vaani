from pathlib import Path
from types import SimpleNamespace

import pytest

from vaani.audio import AudioError
from vaani.platform.audio_common import SoundDeviceRecorder


class _FakeStream:
    def __init__(self, callback):
        self.callback = callback
        self.started = False

    def start(self):
        self.started = True
        # Simulate ~0.3s of silence (16-bit mono @ 16 kHz).
        import array

        samples = array.array("h", [0] * 4800)  # 0.3s
        self.callback(memoryview(samples).cast("B"), 4800, None, None)

    def stop(self):
        return None

    def close(self):
        return None


def test_sounddevice_recorder_writes_wav(tmp_path, monkeypatch):
    sd = SimpleNamespace(
        InputStream=lambda **kwargs: _FakeStream(kwargs["callback"]),
    )
    monkeypatch.setitem(__import__("sys").modules, "sounddevice", sd)
    monkeypatch.setitem(__import__("sys").modules, "numpy", SimpleNamespace())

    rec = SoundDeviceRecorder(tmp_path / "audio")
    started = rec.start()
    assert started.path.exists()
    result = rec.stop()
    assert result.path.exists()
    assert result.duration_seconds >= 0.25


def test_sounddevice_recorder_publishes_live_level(tmp_path, monkeypatch):
    import array

    class LoudStream(_FakeStream):
        def start(self):
            self.started = True
            samples = array.array("h", [12000] * 1600)
            self.callback(memoryview(samples).cast("B"), 1600, None, None)

    sd = SimpleNamespace(InputStream=lambda **kwargs: LoudStream(kwargs["callback"]))
    monkeypatch.setitem(__import__("sys").modules, "sounddevice", sd)
    monkeypatch.setitem(__import__("sys").modules, "numpy", SimpleNamespace())

    amp = tmp_path / "amplitude"
    rec = SoundDeviceRecorder(tmp_path / "audio", amplitude_path=amp)
    rec.start()
    assert rec.level > 0.2
    assert amp.exists()
    assert float(amp.read_text()) > 0.2
    rec.cleanup()


def test_sounddevice_recorder_empty_audio_message(tmp_path, monkeypatch):
    class EmptyStream(_FakeStream):
        def start(self):
            self.started = True

    sd = SimpleNamespace(InputStream=lambda **kwargs: EmptyStream(kwargs["callback"]))
    monkeypatch.setitem(__import__("sys").modules, "sounddevice", sd)
    monkeypatch.setitem(__import__("sys").modules, "numpy", SimpleNamespace())

    rec = SoundDeviceRecorder(tmp_path / "audio")
    rec.start()
    with pytest.raises(AudioError, match="no audio captured"):
        rec.stop()
