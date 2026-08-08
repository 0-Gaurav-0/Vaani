"""In-recording endpointing for snap-started assistant sessions.

The idle snap listener pauses while the mic is recording, so a second snap must
be detected from the growing WAV. Also supports Alexa-style trailing silence.

False starts (lid taps / typing that looked like a snap) are **cancelled**
(Esc-equivalent), not stopped/processed.
"""
from __future__ import annotations

import logging
import math
import os
import struct
import threading
import time
from pathlib import Path
from typing import Callable

from .snap_listener import detect_snap_frames, frame_impulse_features

logger = logging.getLogger("vaani.snap")


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def silence_endpoint_enabled() -> bool:
    raw = os.environ.get("VAANI_SNAP_SILENCE_END", "1").strip().lower()
    return raw in {"1", "true", "yes", "on"}


class SnapSessionWatcher:
    """Watch an open recording WAV; stop on end-snap/silence, or cancel taps."""

    def __init__(
        self,
        path: Path | str,
        on_stop: Callable[[], None],
        *,
        on_cancel: Callable[[], None] | None = None,
        should_run: Callable[[], bool] | None = None,
    ):
        self.path = Path(path)
        self.on_stop = on_stop
        self.on_cancel = on_cancel
        self.should_run = should_run or (lambda: True)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = time.monotonic()
        self._grace_s = _env_float("VAANI_SNAP_STOP_GRACE", 0.85)
        self._silence_s = _env_float("VAANI_SNAP_SILENCE_S", 2.0)
        self._speech_floor = _env_float("VAANI_SNAP_SPEECH_FLOOR", 0.018)
        self._abs_threshold = _env_float("VAANI_SNAP_THRESHOLD", 0.07)
        self._ratio = _env_float("VAANI_SNAP_RATIO", 9.0)
        self._baseline = 0.002
        self._heard_speech = False
        self._speech_run = 0
        self._silent_frames = 0
        self._tap_events = 0
        self._recent_hp: list[float] = []
        self._recent_crest: list[float] = []
        self._recent_zcr: list[float] = []
        self._offset = 44  # skip RIFF header; advance as we read
        self._fired = False
        self._frame_s = 160 / 16000.0

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._started = time.monotonic()
        self._thread = threading.Thread(
            target=self._run, name="vaani-snap-endpoint", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)

    def _fire_stop(self, reason: str) -> None:
        if self._fired:
            return
        self._fired = True
        self._stop.set()
        logger.info("event=snap_endpoint reason=%s", reason)
        try:
            self.on_stop()
        except Exception:
            logger.exception("snap endpoint on_stop failed")

    def _fire_cancel(self, reason: str) -> None:
        if self._fired:
            return
        self._fired = True
        self._stop.set()
        logger.info("event=snap_endpoint reason=%s action=cancel", reason)
        cb = self.on_cancel
        if cb is None:
            # Fallback: still tear down via stop if cancel not wired.
            try:
                self.on_stop()
            except Exception:
                logger.exception("snap endpoint on_stop failed")
            return
        try:
            cb()
        except Exception:
            logger.exception("snap endpoint on_cancel failed")

    def _run(self) -> None:
        silence_on = silence_endpoint_enabled()
        while not self._stop.wait(0.04):
            if not self.should_run():
                continue
            elapsed = time.monotonic() - self._started
            try:
                with open(self.path, "rb") as fh:
                    fh.seek(0, os.SEEK_END)
                    size = fh.tell()
                    if size <= self._offset:
                        continue
                    fh.seek(self._offset)
                    data = fh.read()
                    self._offset += len(data)
            except OSError:
                continue
            if len(data) < 4:
                continue
            # Process in 10ms frames (160 samples).
            usable = data[: len(data) - (len(data) % 2)]
            samples = list(struct.unpack("<%dh" % (len(usable) // 2), usable))
            frame = 160
            for i in range(0, len(samples) - frame + 1, frame):
                chunk = samples[i : i + frame]
                hp, crest, zcr = frame_impulse_features(chunk)
                broadband = min(
                    1.0,
                    math.sqrt(sum(s * s for s in chunk) / frame) / 32768.0,
                )
                if hp < self._baseline * 2.2:
                    self._baseline = (0.96 * self._baseline) + (0.04 * max(hp, 1e-5))
                self._recent_hp.append(hp)
                self._recent_crest.append(crest)
                self._recent_zcr.append(zcr)
                if len(self._recent_hp) > 14:
                    self._recent_hp.pop(0)
                    self._recent_crest.pop(0)
                    self._recent_zcr.pop(0)

                # Sustained broadband energy ⇒ speech. Lid/key taps are brief
                # spikes that never hold this long.
                if broadband >= self._speech_floor:
                    self._speech_run += 1
                    if self._speech_run >= 12:  # ~120ms sustained
                        self._heard_speech = True
                    self._silent_frames = 0
                else:
                    self._speech_run = 0
                    self._silent_frames += 1

                impulse = detect_snap_frames(
                    self._recent_hp,
                    baseline=self._baseline,
                    abs_threshold=self._abs_threshold,
                    ratio=self._ratio,
                    frames_crest=self._recent_crest,
                    frames_zcr=self._recent_zcr,
                )
                if impulse:
                    self._recent_hp.clear()
                    self._recent_crest.clear()
                    self._recent_zcr.clear()
                    # After a false snap-start, more lid/key taps arrive without
                    # speech — Esc-cancel instead of completing the session.
                    if elapsed >= 0.28 and not self._heard_speech:
                        self._tap_events += 1
                        if self._tap_events >= 2:
                            self._fire_cancel("tap_burst")
                            return
                        continue
                    if elapsed >= self._grace_s and self._heard_speech:
                        self._fire_stop("snap_stop")
                        return

                if not silence_on:
                    continue
                # No speech ever + quiet for a bit → cancel false start.
                if (
                    not self._heard_speech
                    and elapsed >= 1.15
                    and self._silent_frames * self._frame_s >= 0.55
                ):
                    # Only if we've been mostly quiet (not mid-tap train).
                    if max(self._recent_hp or [0.0]) < self._abs_threshold * 0.5:
                        self._fire_cancel("no_speech")
                        return
                if (
                    self._heard_speech
                    and elapsed >= self._grace_s
                    and self._silent_frames * self._frame_s >= self._silence_s
                ):
                    self._fire_stop("silence")
                    return
