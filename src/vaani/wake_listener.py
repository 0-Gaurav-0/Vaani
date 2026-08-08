"""Idle-mic wake listener: speech → short STT → “hey Vaani” → assistant."""
from __future__ import annotations

import logging
import os
import struct
import subprocess
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import Callable

from .config import child_environment
from .snap_listener import (
    _FRAME_BYTES,
    _FRAME_SAMPLES,
    _MONITOR_ARGV,
    _SAMPLE_RATE,
    detect_snap_frames,
    highpass_rms,
)
from .wake_phrase import extract_wake_assistant

logger = logging.getLogger("vaani.wake")


def wake_assistant_enabled() -> bool:
    raw = os.environ.get("VAANI_WAKE_ASSISTANT", "1").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _write_wav(path: Path, pcm: bytes) -> None:
    n = len(pcm) // 2
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(_SAMPLE_RATE)
        wf.writeframes(pcm[: n * 2])


class WakeListener:
    """Share the idle mic stream: finger-snap + hey-Vaani wake phrasing."""

    def __init__(
        self,
        *,
        on_snap: Callable[[], None] | None = None,
        on_wake: Callable[[str], None] | None = None,
        transcribe: Callable[[Path], str] | None = None,
        should_listen: Callable[[], bool] | None = None,
        snap_enabled: bool = True,
        wake_enabled: bool = True,
        parec: str = "parec",
    ):
        self.on_snap = on_snap
        self.on_wake = on_wake
        self.transcribe = transcribe
        self.should_listen = should_listen or (lambda: True)
        self.snap_enabled = snap_enabled
        self.wake_enabled = wake_enabled
        self.parec = parec
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._proc: subprocess.Popen[bytes] | None = None
        self._cooldown_s = _env_float("VAANI_SNAP_COOLDOWN", 0.55)
        self._abs_threshold = _env_float("VAANI_SNAP_THRESHOLD", 0.045)
        self._ratio = _env_float("VAANI_SNAP_RATIO", 6.0)
        self._speech_floor = _env_float("VAANI_WAKE_SPEECH_FLOOR", 0.02)
        self._end_silence_s = _env_float("VAANI_WAKE_END_SILENCE_S", 0.55)
        self._min_utt_s = _env_float("VAANI_WAKE_MIN_S", 0.35)
        self._max_utt_s = _env_float("VAANI_WAKE_MAX_S", 6.0)
        self._wake_cooldown_s = _env_float("VAANI_WAKE_COOLDOWN", 2.0)
        self._last_snap = 0.0
        self._last_wake = 0.0
        self._baseline = 0.002
        self._transcribe_lock = threading.Lock()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="vaani-wake-listener", daemon=True
        )
        self._thread.start()
        logger.info(
            "event=wake_listener_start snap=%s wake=%s",
            self.snap_enabled,
            self.wake_enabled and self.transcribe is not None and self.on_wake is not None,
        )

    def stop(self) -> None:
        self._stop.set()
        self._kill_proc()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.5)
        self._thread = None

    def _kill_proc(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.terminate()
        except Exception:
            pass
        try:
            proc.wait(timeout=0.4)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def _open_proc(self) -> subprocess.Popen[bytes] | None:
        self._kill_proc()
        argv = (self.parec,) + _MONITOR_ARGV[1:]
        try:
            proc = subprocess.Popen(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=child_environment(),
            )
        except OSError as exc:
            logger.warning("event=wake_parec_failed detail=%s", type(exc).__name__)
            return None
        self._proc = proc
        return proc

    def _fire_snap(self) -> None:
        if not self.on_snap:
            return
        now = time.monotonic()
        if now - self._last_snap < self._cooldown_s:
            return
        self._last_snap = now
        logger.info("event=snap_detected")
        try:
            self.on_snap()
        except Exception:
            logger.exception("snap callback failed")

    def _maybe_wake(self, pcm: bytes) -> None:
        if not self.wake_enabled or not self.transcribe or not self.on_wake:
            return
        now = time.monotonic()
        if now - self._last_wake < self._wake_cooldown_s:
            return
        if now - self._last_snap < 1.0:
            return  # don't STT the snap itself
        dur = len(pcm) / (2 * _SAMPLE_RATE)
        if dur < self._min_utt_s or dur > self._max_utt_s:
            return
        if not self._transcribe_lock.acquire(blocking=False):
            return
        try:
            fd, name = tempfile.mkstemp(prefix="vaani-wake-", suffix=".wav")
            os.close(fd)
            path = Path(name)
            try:
                _write_wav(path, pcm)
                text = (self.transcribe(path) or "").strip()
            finally:
                path.unlink(missing_ok=True)
            if not text:
                return
            payload = extract_wake_assistant(text)
            if payload is None:
                logger.info(
                    "event=wake_miss preview=%r",
                    (text[:80] + "…") if len(text) > 80 else text,
                )
                return
            self._last_wake = time.monotonic()
            logger.info(
                "event=wake_detected chars=%s payload=%r preview=%r",
                len(payload),
                (payload[:80] + "…") if len(payload) > 80 else payload,
                (text[:80] + "…") if len(text) > 80 else text,
            )
            self.on_wake(payload)
        except Exception:
            logger.exception("wake transcribe failed")
        finally:
            self._transcribe_lock.release()

    def _run(self) -> None:
        recent_hp: list[float] = []
        buf = b""
        speech_pcm = bytearray()
        pre_roll = bytearray()
        pre_roll_max = int(0.35 * _SAMPLE_RATE) * 2
        in_speech = False
        silent_frames = 0
        end_silence_frames = max(1, int(self._end_silence_s / 0.01))

        while not self._stop.is_set():
            if not self.should_listen():
                self._kill_proc()
                recent_hp.clear()
                buf = b""
                speech_pcm.clear()
                pre_roll.clear()
                in_speech = False
                silent_frames = 0
                self._stop.wait(0.15)
                continue
            proc = self._proc
            if proc is None or proc.poll() is not None:
                proc = self._open_proc()
                if proc is None or proc.stdout is None:
                    self._stop.wait(0.5)
                    continue
                buf = b""
                recent_hp.clear()
            try:
                chunk = proc.stdout.read(_FRAME_BYTES)
            except Exception:
                self._kill_proc()
                continue
            if not chunk:
                self._kill_proc()
                self._stop.wait(0.1)
                continue
            buf += chunk
            while len(buf) >= _FRAME_BYTES:
                frame = buf[:_FRAME_BYTES]
                buf = buf[_FRAME_BYTES:]
                samples = list(struct.unpack(f"<{_FRAME_SAMPLES}h", frame))
                hp = highpass_rms(samples)
                broadband = min(
                    1.0,
                    (sum(s * s for s in samples) / len(samples)) ** 0.5 / 32768.0,
                )
                if hp < self._baseline * 2.5:
                    self._baseline = (0.95 * self._baseline) + (0.05 * max(hp, 1e-5))
                recent_hp.append(hp)
                if len(recent_hp) > 12:
                    recent_hp.pop(0)

                if self.snap_enabled and detect_snap_frames(
                    recent_hp,
                    baseline=self._baseline,
                    abs_threshold=self._abs_threshold,
                    ratio=self._ratio,
                ):
                    recent_hp.clear()
                    speech_pcm.clear()
                    in_speech = False
                    silent_frames = 0
                    self._fire_snap()
                    try:
                        if proc.stdout is not None:
                            proc.stdout.read(_FRAME_BYTES * 8)
                    except Exception:
                        pass
                    continue

                # Wake: accumulate speech, STT on trailing silence.
                if not self.wake_enabled:
                    continue
                if broadband >= self._speech_floor:
                    if not in_speech:
                        in_speech = True
                        speech_pcm = bytearray(pre_roll)
                    speech_pcm.extend(frame)
                    silent_frames = 0
                    if len(speech_pcm) > int(self._max_utt_s * _SAMPLE_RATE) * 2:
                        # Too long — drop (likely conversation, not a wake).
                        in_speech = False
                        speech_pcm.clear()
                else:
                    pre_roll.extend(frame)
                    if len(pre_roll) > pre_roll_max:
                        pre_roll = pre_roll[-pre_roll_max:]
                    if in_speech:
                        speech_pcm.extend(frame)
                        silent_frames += 1
                        if silent_frames >= end_silence_frames:
                            pcm = bytes(speech_pcm)
                            in_speech = False
                            speech_pcm.clear()
                            silent_frames = 0
                            threading.Thread(
                                target=self._maybe_wake,
                                args=(pcm,),
                                name="vaani-wake-stt",
                                daemon=True,
                            ).start()
