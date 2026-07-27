"""Secure PulseAudio (parec) recording lifecycle and deterministic checks."""
from __future__ import annotations

import math
import os
import signal
import struct
import subprocess
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import Mapping, Sequence

from .config import MAX_AUDIO_BYTES, MAX_RECORDING_SECONDS, MIN_RECORDING_SECONDS, child_environment
from .config import sweep_audio_directory
from .types import AudioResult

PAREC_ARGV = ("parec", "--device=@DEFAULT_SOURCE@", "--rate=16000", "--channels=1", "--format=s16le", "--file-format=wav")
PAREC_CMDLINE = " ".join(PAREC_ARGV)


class AudioError(RuntimeError):
    pass


class AudioPreflightError(AudioError):
    pass


def reap_orphan_parec(*, keep_pid: int | None = None) -> int:
    """Kill leftover Vaani ``parec`` processes so the mic is released.

    Crashes / SIGKILL can leave ``parec`` recording forever (OS privacy LED
    stays on). Safe to call at startup and shutdown; only matches Vaani's
    exact argv for the current user.
    """
    killed = 0
    try:
        out = subprocess.check_output(
            ["ps", "-u", str(os.getuid()), "-o", "pid=,args="],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return 0
    for line in out.splitlines():
        raw = line.strip()
        if not raw:
            continue
        pid_s, _, args = raw.partition(" ")
        try:
            pid = int(pid_s)
        except ValueError:
            continue
        if keep_pid is not None and pid == keep_pid:
            continue
        cmdline = args.strip()
        if cmdline != PAREC_CMDLINE and not cmdline.endswith(" " + PAREC_CMDLINE):
            # Also match when argv0 is an absolute path to parec.
            if " --device=@DEFAULT_SOURCE@ --rate=16000 --channels=1 --format=s16le --file-format=wav" not in cmdline:
                continue
            if "parec" not in cmdline.split()[0] and "/parec" not in cmdline.split()[0]:
                continue
        try:
            os.kill(pid, signal.SIGTERM)
            killed += 1
        except OSError:
            continue
    if killed:
        # Give them a moment, then force any stubborn leftovers.
        time.sleep(0.15)
        try:
            out = subprocess.check_output(
                ["ps", "-u", str(os.getuid()), "-o", "pid=,args="],
                text=True,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.SubprocessError):
            out = ""
        for line in out.splitlines():
            raw = line.strip()
            if not raw:
                continue
            pid_s, _, args = raw.partition(" ")
            try:
                pid = int(pid_s)
            except ValueError:
                continue
            if keep_pid is not None and pid == keep_pid:
                continue
            cmdline = args.strip()
            if " --device=@DEFAULT_SOURCE@ --rate=16000 --channels=1 --format=s16le --file-format=wav" not in cmdline:
                continue
            if "parec" not in cmdline:
                continue
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        try:
            import logging

            logging.getLogger("vaani").info("event=mic_reap orphans=%s", killed)
        except Exception:
            pass
    return killed


def _pcm16_rms(data: bytes) -> float:
    if len(data) < 2:
        return 0.0
    n = len(data) // 2
    vals = struct.unpack("<%dh" % n, data[: n * 2])
    return min(1.0, math.sqrt(sum(v * v for v in vals) / n) / 32768.0)


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
    def __init__(
        self,
        audio_dir: Path,
        *,
        parec: str = "parec",
        env: Mapping[str, str] | None = None,
        amplitude_path: Path | str | None = None,
    ):
        self.audio_dir = Path(audio_dir)
        if os.path.lexists(self.audio_dir) and (self.audio_dir.is_symlink() or not self.audio_dir.is_dir()):
            raise AudioError("audio directory is not a safe directory")
        if self.audio_dir.is_dir():
            sweep_audio_directory(self.audio_dir)
        self.parec = parec
        self.env = child_environment(env)
        self.amplitude_path = Path(amplitude_path) if amplitude_path is not None else None
        self._proc: subprocess.Popen[bytes] | None = None
        self._file = None
        self._path: Path | None = None
        self._started = 0.0
        self._stopped: AudioResult | None = None
        self._level = 0.0
        self._level_stop = threading.Event()
        self._level_thread: threading.Thread | None = None

    @property
    def level(self) -> float:
        """Latest normalized RMS mic level (0..1) from the growing WAV tail."""
        return self._level

    def _start_level_monitor(self) -> None:
        self._level_stop.clear()
        path = self._path
        amp_path = self.amplitude_path

        def monitor() -> None:
            while not self._level_stop.wait(0.04):
                if path is None:
                    continue
                try:
                    with open(path, "rb") as fh:
                        fh.seek(44)
                        data = fh.read()[-4096:]
                    if len(data) >= 2:
                        level = _pcm16_rms(data)
                        self._level = level
                        if amp_path is not None:
                            try:
                                amp_path.parent.mkdir(parents=True, exist_ok=True)
                                tmp = amp_path.with_suffix(".tmp")
                                tmp.write_text(f"{level:.4f}", encoding="utf-8")
                                os.replace(tmp, amp_path)
                            except OSError:
                                pass
                except Exception:
                    pass

        self._level_thread = threading.Thread(target=monitor, daemon=True)
        self._level_thread.start()

    def _clear_amplitude(self) -> None:
        amp_path = self.amplitude_path
        if amp_path is None:
            return
        try:
            amp_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = amp_path.with_suffix(".tmp")
            tmp.write_text("0.0000", encoding="utf-8")
            os.replace(tmp, amp_path)
        except OSError:
            pass

    def _stop_level_monitor(self) -> None:
        self._level_stop.set()
        self._level = 0.0
        thread = self._level_thread
        self._level_thread = None
        if thread is not None and thread.is_alive():
            try:
                thread.join(timeout=0.5)
            except Exception:
                pass
        self._clear_amplitude()

    def start(self) -> AudioResult:
        self._stopped = None
        if self._proc is not None:
            raise AudioError("recording already active")
        # Never leave a prior crash holding the mic open.
        reap_orphan_parec()
        self.audio_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.audio_dir.chmod(0o700)
        fd, path = tempfile.mkstemp(prefix="recording-", suffix=".wav", dir=self.audio_dir)
        os.fchmod(fd, 0o600)
        file = os.fdopen(fd, "wb")
        try:
            self._proc = subprocess.Popen(
                [self.parec, *PAREC_ARGV[1:]],
                shell=False,
                stdout=file,
                stderr=subprocess.DEVNULL,
                env=self.env,
            )
        except Exception as exc:
            file.close()
            Path(path).unlink(missing_ok=True)
            raise AudioError(f"source open failed: {exc}") from exc
        self._file, self._path, self._started = file, Path(path), time.monotonic()
        self._level = 0.0
        # Brief crash watch: real parec fails quickly if the source is missing.
        deadline = time.monotonic() + 0.05
        code = self._proc.poll()
        while code is None and time.monotonic() < deadline:
            time.sleep(0.01)
            code = self._proc.poll()
        if code not in (None, 0):
            self.cleanup()
            self._proc = None
            raise AudioError(f"source open failed (exit {code})")
        self._start_level_monitor()
        try:
            import logging

            logging.getLogger("vaani").info("event=mic_open")
        except Exception:
            pass
        return AudioResult(self._path, 0.0)

    def stop(self) -> AudioResult:
        if self._stopped is not None:
            return self._stopped
        if self._proc is None or self._path is None:
            raise AudioError("recording not active")
        self._stop_level_monitor()
        proc, path, started = self._proc, self._path, self._started
        escalated = False
        stop_initiated = time.monotonic()
        try:
            if proc.poll() is None:
                proc.send_signal(signal.SIGINT)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                escalated = True
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    escalated = True
                    proc.kill()
                    proc.wait()
        finally:
            if self._file:
                self._file.close()
            self._proc = None
        try:
            import logging

            logging.getLogger("vaani").info("event=mic_close")
        except Exception:
            pass
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
        self._stop_level_monitor()
        if self._proc is not None:
            if self._proc.poll() is None:
                try:
                    self._proc.terminate()
                    self._proc.wait(timeout=2)
                except Exception:
                    try:
                        self._proc.kill()
                        self._proc.wait(timeout=2)
                    except Exception:
                        pass
            else:
                try:
                    self._proc.wait(timeout=0)
                except Exception:
                    pass
            self._proc = None
            try:
                import logging

                logging.getLogger("vaani").info("event=mic_close reason=cleanup")
            except Exception:
                pass
        if self._file:
            self._file.close()
            self._file = None
        if self._path:
            self._path.unlink(missing_ok=True)
            self._path = None
        # Belt-and-suspenders: never leave a Vaani parec behind after cleanup.
        reap_orphan_parec()


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
