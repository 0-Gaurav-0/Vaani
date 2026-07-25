"""Secure PulseAudio (parec) recording lifecycle and deterministic checks."""
from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import time
import wave
from pathlib import Path
from typing import Mapping, Sequence

from .config import MAX_AUDIO_BYTES, MAX_RECORDING_SECONDS, MIN_RECORDING_SECONDS, child_environment
from .config import sweep_audio_directory
from .types import AudioResult

PAREC_ARGV = ("parec", "--device=@DEFAULT_SOURCE@", "--rate=16000", "--channels=1", "--format=s16le", "--file-format=wav")


class AudioError(RuntimeError):
    pass


class AudioPreflightError(AudioError):
    pass


def _run(argv: Sequence[str], *, env: Mapping[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(argv), shell=False, check=False, capture_output=True, text=True, env=child_environment(env), timeout=5)


def preflight(*, parec: str = "parec", pactl: str = "pactl", env: Mapping[str, str] | None = None) -> None:
    """Check installed recorder and default source without opening microphone."""
    for args, label in (((parec, "--help"), "parec --help"), ((parec, "--list-file-formats"), "parec --list-file-formats")):
        result = _run(args, env=env)
        if result.returncode != 0:
            raise AudioPreflightError(f"{label} failed")
        if label.endswith("list-file-formats") and "wav" not in (result.stdout + result.stderr).lower():
            raise AudioPreflightError("parec does not support wav")
    result = _run((pactl, "get-default-source"), env=env)
    if result.returncode != 0 or not result.stdout.strip():
        raise AudioPreflightError("default PulseAudio source unavailable")


class AudioRecorderImpl:
    def __init__(self, audio_dir: Path, *, parec: str = "parec", env: Mapping[str, str] | None = None):
        self.audio_dir = Path(audio_dir)
        if os.path.lexists(self.audio_dir) and (self.audio_dir.is_symlink() or not self.audio_dir.is_dir()):
            raise AudioError("audio directory is not a safe directory")
        if self.audio_dir.is_dir():
            sweep_audio_directory(self.audio_dir)
        self.parec = parec
        self.env = child_environment(env)
        self._proc: subprocess.Popen[bytes] | None = None
        self._file = None
        self._path: Path | None = None
        self._started = 0.0
        self._stopped: AudioResult | None = None

    def start(self) -> AudioResult:
        self._stopped = None
        if self._proc is not None:
            raise AudioError("recording already active")
        if self._proc is not None:
            raise AudioError("recording already active")
        self.audio_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.audio_dir.chmod(0o700)
        fd, path = tempfile.mkstemp(prefix="recording-", suffix=".wav", dir=self.audio_dir)
        os.fchmod(fd, 0o600)
        file = os.fdopen(fd, "wb")
        try:
            self._proc = subprocess.Popen([self.parec, *PAREC_ARGV[1:]], shell=False, stdout=file, stderr=subprocess.DEVNULL, env=self.env)
        except Exception as exc:
            file.close(); Path(path).unlink(missing_ok=True)
            raise AudioError(f"source open failed: {exc}") from exc
        self._file, self._path, self._started = file, Path(path), time.monotonic()
        time.sleep(0.01)
        if self._proc.poll() not in (None, 0):
            code = self._proc.returncode
            self.cleanup(); self._proc = None
            raise AudioError(f"source open failed (exit {code})")
        return AudioResult(self._path, 0.0)

    def stop(self) -> AudioResult:
        if self._stopped is not None:
            return self._stopped
        if self._proc is None or self._path is None:
            raise AudioError("recording not active")
        proc, path, started = self._proc, self._path, self._started
        escalated = False
        stop_initiated = time.monotonic()
        try:
            if proc.poll() is None:
                proc.send_signal(signal.SIGINT)
            try: proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                escalated = True; proc.terminate()
                try: proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    escalated = True; proc.kill(); proc.wait()
        finally:
            if self._file: self._file.close()
            self._proc = None
        duration = max(0.0, stop_initiated - started)
        try:
            result = validate_wav(path, duration_seconds=duration)
            if escalated or proc.returncode not in (0, 130, -signal.SIGINT):
                raise AudioError("recording process failed")
            self._stopped = result
            return result
        except Exception:
            path.unlink(missing_ok=True)
            raise

    def cleanup(self) -> None:
        """Delete the temporary recording; safe to call repeatedly."""
        if self._proc is not None:
            if self._proc.poll() is None:
                try: self._proc.terminate(); self._proc.wait(timeout=2)
                except Exception:
                    try: self._proc.kill(); self._proc.wait(timeout=2)
                    except Exception: pass
            else:
                try: self._proc.wait(timeout=0)
                except Exception: pass
            self._proc = None
        if self._file:
            self._file.close()
            self._file = None
        if self._path:
            self._path.unlink(missing_ok=True)
            self._path = None


def validate_wav(path: Path, *, duration_seconds: float | None = None) -> AudioResult:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_AUDIO_BYTES:
        raise AudioError("invalid audio file")
    try:
        with wave.open(str(path), "rb") as wav:
            if wav.getnchannels() != 1 or wav.getframerate() != 16000 or wav.getsampwidth() != 2:
                raise AudioError("invalid WAV format")
            frames, rate = wav.getnframes(), wav.getframerate()
            duration = frames / rate
    except (wave.Error, EOFError) as exc:
        raise AudioError("invalid WAV format") from exc
    if duration < MIN_RECORDING_SECONDS or duration > MAX_RECORDING_SECONDS:
        raise AudioError("recording duration out of range")
    return AudioResult(path, duration_seconds if duration_seconds is not None else duration)


AudioRecorder = AudioRecorderImpl
ParecRecorder = AudioRecorderImpl
run_preflight = preflight
