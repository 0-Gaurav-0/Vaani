"""Portable sounddevice-based WAV recorder shared by macOS and Windows."""
from __future__ import annotations

import os
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import Any

from ..audio import AudioError, AudioPreflightError, validate_wav
from ..config import MAX_RECORDING_SECONDS, MIN_RECORDING_SECONDS, sweep_audio_directory
from ..types import AudioResult

SAMPLE_RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2  # 16-bit


class SoundDeviceRecorder:
    """Record 16 kHz mono PCM to a temporary WAV via the sounddevice package."""

    def __init__(self, audio_dir: Path, *, device: Any = None):
        self.audio_dir = Path(audio_dir)
        if os.path.lexists(self.audio_dir) and (
            self.audio_dir.is_symlink() or not self.audio_dir.is_dir()
        ):
            raise AudioError("audio directory is not a safe directory")
        if self.audio_dir.is_dir():
            sweep_audio_directory(self.audio_dir)
        self.device = device
        self._stream: Any = None
        self._path: Path | None = None
        self._wave: wave.Wave_write | None = None
        self._started = 0.0
        self._lock = threading.Lock()
        self._frames = 0

    def start(self) -> AudioResult:
        with self._lock:
            if self._stream is not None:
                raise AudioError("recording already active")
            try:
                import sounddevice as sd
            except ImportError as exc:
                raise AudioPreflightError(
                    "sounddevice is not installed; pip install 'vaani[macos]' or 'vaani[windows]'"
                ) from exc
            self.audio_dir.mkdir(parents=True, exist_ok=True)
            try:
                if hasattr(self.audio_dir, "chmod"):
                    self.audio_dir.chmod(0o700)
            except OSError:
                pass
            fd, path = tempfile.mkstemp(prefix="recording-", suffix=".wav", dir=self.audio_dir)
            try:
                os.fchmod(fd, 0o600)
            except (OSError, AttributeError):
                pass
            self._path = Path(path)
            self._wave = wave.open(os.fdopen(fd, "wb"), "wb")
            self._wave.setnchannels(CHANNELS)
            self._wave.setsampwidth(SAMPLE_WIDTH)
            self._wave.setframerate(SAMPLE_RATE)
            self._frames = 0

            def callback(indata, frames, time_info, status):  # noqa: ARG001
                if self._wave is None:
                    return
                try:
                    self._wave.writeframes(indata.tobytes())
                    self._frames += frames
                except Exception:
                    pass

            try:
                self._stream = sd.RawInputStream(
                    samplerate=SAMPLE_RATE,
                    channels=CHANNELS,
                    dtype="int16",
                    device=self.device,
                    callback=callback,
                )
                self._stream.start()
            except Exception as exc:
                self.cleanup()
                raise AudioError(f"source open failed: {exc}") from exc
            self._started = time.monotonic()
            return AudioResult(self._path, 0.0)

    def stop(self) -> AudioResult:
        with self._lock:
            if self._stream is None or self._path is None:
                raise AudioError("recording is not active")
            try:
                self._stream.stop()
                self._stream.close()
            finally:
                self._stream = None
                if self._wave is not None:
                    try:
                        self._wave.close()
                    except Exception:
                        pass
                    self._wave = None
            duration = max(time.monotonic() - self._started, 0.0)
            path = self._path
            self._path = None
        if duration < MIN_RECORDING_SECONDS:
            path.unlink(missing_ok=True)
            raise AudioError("recording too short")
        if duration > MAX_RECORDING_SECONDS:
            path.unlink(missing_ok=True)
            raise AudioError("recording too long")
        return validate_wav(path, duration_seconds=duration)

    def cleanup(self) -> None:
        with self._lock:
            if self._stream is not None:
                try:
                    self._stream.stop()
                    self._stream.close()
                except Exception:
                    pass
                self._stream = None
            if self._wave is not None:
                try:
                    self._wave.close()
                except Exception:
                    pass
                self._wave = None
            if self._path is not None:
                try:
                    self._path.unlink(missing_ok=True)
                except OSError:
                    pass
                self._path = None
