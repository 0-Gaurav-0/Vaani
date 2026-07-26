"""Portable sounddevice-based WAV recorder shared by macOS and Windows."""
from __future__ import annotations

import math
import os
import struct
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


def _pcm16_rms(data: bytes) -> float:
    if len(data) < 2:
        return 0.0
    n = len(data) // 2
    vals = struct.unpack("<%dh" % n, data[: n * 2])
    return min(1.0, math.sqrt(sum(v * v for v in vals) / n) / 32768.0)


class SoundDeviceRecorder:
    """Record 16 kHz mono PCM to a temporary WAV via the sounddevice package.

    Audio is buffered in memory from the PortAudio callback and written to a
    WAV only on ``stop()``. Writing the wave file from the realtime callback
    is unsafe and often yields empty files on macOS.

    Live mic level is exposed via ``level`` and optionally written to
    ``amplitude_path`` so the recording pill can animate while capturing.
    """

    def __init__(
        self,
        audio_dir: Path,
        *,
        device: Any = None,
        amplitude_path: Path | str | None = None,
    ):
        self.audio_dir = Path(audio_dir)
        if os.path.lexists(self.audio_dir) and (
            self.audio_dir.is_symlink() or not self.audio_dir.is_dir()
        ):
            raise AudioError("audio directory is not a safe directory")
        if self.audio_dir.is_dir():
            sweep_audio_directory(self.audio_dir)
        self.device = device
        self.amplitude_path = Path(amplitude_path) if amplitude_path is not None else None
        self._stream: Any = None
        self._path: Path | None = None
        self._started = 0.0
        self._lock = threading.Lock()
        self._chunks: list[bytes] = []
        self._callback_error: str | None = None
        self._level = 0.0
        self._level_written = 0.0

    @property
    def level(self) -> float:
        """Latest normalized RMS mic level (0..1), updated from the audio callback."""
        return self._level

    def start(self) -> AudioResult:
        with self._lock:
            if self._stream is not None:
                raise AudioError("recording already active")
            try:
                import numpy as np  # noqa: F401
                import sounddevice as sd
            except ImportError as exc:
                raise AudioPreflightError(
                    "sounddevice/numpy missing; pip install 'vaani[macos]' or 'vaani[windows]'"
                ) from exc

            self.audio_dir.mkdir(parents=True, exist_ok=True)
            try:
                self.audio_dir.chmod(0o700)
            except OSError:
                pass
            fd, path = tempfile.mkstemp(prefix="recording-", suffix=".wav", dir=self.audio_dir)
            try:
                os.fchmod(fd, 0o600)
            except (OSError, AttributeError):
                pass
            os.close(fd)
            self._path = Path(path)
            self._chunks = []
            self._callback_error = None
            self._level = 0.0
            self._level_written = 0.0
            amp_path = self.amplitude_path

            def callback(indata, frames, time_info, status):  # noqa: ARG001
                try:
                    if status:
                        self._callback_error = str(status)
                    # Prefer numpy RMS (accurate); fall back to raw PCM bytes.
                    try:
                        import numpy as np

                        arr = np.asarray(indata, dtype=np.float32).reshape(-1)
                        level = float(min(1.0, np.sqrt(np.mean(arr * arr)) / 32768.0))
                        chunk = np.asarray(indata, dtype=np.int16).tobytes()
                    except Exception:
                        chunk = (
                            indata.tobytes()
                            if hasattr(indata, "tobytes")
                            else bytes(indata)
                        )
                        level = _pcm16_rms(chunk)
                    self._chunks.append(chunk)
                    self._level = level
                    if amp_path is not None:
                        now = time.monotonic()
                        if now - self._level_written >= 0.03:
                            self._level_written = now
                            try:
                                amp_path.parent.mkdir(parents=True, exist_ok=True)
                                tmp = amp_path.with_suffix(".tmp")
                                tmp.write_text(f"{level:.4f}", encoding="utf-8")
                                os.replace(tmp, amp_path)
                            except OSError:
                                pass
                except Exception as exc:  # pragma: no cover - defensive
                    self._callback_error = repr(exc)

            try:
                self._stream = sd.InputStream(
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
            # Mic is open only between start() and stop()/cleanup().
            try:
                import logging

                logging.getLogger("vaani").info("event=mic_open")
            except Exception:
                pass
            return AudioResult(self._path, 0.0)

    @staticmethod
    def _release_stream(stream: Any) -> None:
        """Fully release the PortAudio capture device (privacy-critical)."""
        if stream is None:
            return
        for method_name in ("abort", "stop", "close"):
            method = getattr(stream, method_name, None)
            if not callable(method):
                continue
            try:
                method()
            except Exception:
                pass

    def stop(self) -> AudioResult:
        with self._lock:
            if self._stream is None or self._path is None:
                raise AudioError("recording is not active")
            stream = self._stream
            path = self._path
            self._stream = None
            self._path = None
            chunks = self._chunks
            self._chunks = []
            callback_error = self._callback_error
            started = self._started
            self._level = 0.0
        self._release_stream(stream)
        try:
            import logging

            logging.getLogger("vaani").info("event=mic_close")
        except Exception:
            pass
        # Give the callback thread a beat to finish the last block.
        time.sleep(0.05)
        wall = max(time.monotonic() - started, 0.0)
        pcm = b"".join(chunks)
        if not pcm:
            path.unlink(missing_ok=True)
            detail = callback_error or "no samples received"
            raise AudioError(
                f"no audio captured ({detail}). "
                "Grant Microphone access to Terminal/Cursor in "
                "System Settings → Privacy & Security → Microphone, then retry."
            )
        frames = len(pcm) // (CHANNELS * SAMPLE_WIDTH)
        file_duration = frames / float(SAMPLE_RATE)
        try:
            with wave.open(str(path), "wb") as wav:
                wav.setnchannels(CHANNELS)
                wav.setsampwidth(SAMPLE_WIDTH)
                wav.setframerate(SAMPLE_RATE)
                wav.writeframes(pcm)
        except Exception as exc:
            path.unlink(missing_ok=True)
            raise AudioError(f"failed to write WAV: {exc}") from exc
        if file_duration < MIN_RECORDING_SECONDS:
            path.unlink(missing_ok=True)
            raise AudioError(
                "recording too short — speak for at least half a second before stopping"
            )
        if wall > MAX_RECORDING_SECONDS or file_duration > MAX_RECORDING_SECONDS:
            path.unlink(missing_ok=True)
            raise AudioError("recording too long")
        return validate_wav(path, duration_seconds=file_duration)

    def cleanup(self) -> None:
        with self._lock:
            stream = self._stream
            self._stream = None
            path = self._path
            self._path = None
            self._chunks = []
            self._level = 0.0
        if stream is not None:
            self._release_stream(stream)
            try:
                import logging

                logging.getLogger("vaani").info("event=mic_close reason=cleanup")
            except Exception:
                pass
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
