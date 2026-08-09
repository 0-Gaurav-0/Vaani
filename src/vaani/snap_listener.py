"""Finger-snap / clap detector for hands-free assistant mode toggle.

Listens on the default mic only while idle (paused during RECORDING/PROCESSING
so it never fights the dictation ``parec``).

Detection is intentionally strict: a snap/clap is a *transient impulse* —
quiet surroundings, a near-instant attack, a brief peak, and a fast decay.
Loud speech, shouts, and room noise have longer envelopes and fail those
checks even when they are high-energy.
"""
from __future__ import annotations

import logging
import math
import os
import struct
import subprocess
import threading
import time
from typing import Callable

from .config import child_environment

logger = logging.getLogger("vaani.snap")

# Raw PCM monitor (no WAV header) — distinct from AudioRecorderImpl's WAV parec.
_MONITOR_ARGV = (
    "parec",
    "--device=@DEFAULT_SOURCE@",
    "--rate=16000",
    "--channels=1",
    "--format=s16le",
)

_SAMPLE_RATE = 16000
_FRAME_SAMPLES = 160  # 10 ms
_FRAME_BYTES = _FRAME_SAMPLES * 2


def snap_assistant_enabled() -> bool:
    raw = os.environ.get("VAANI_SNAP_ASSISTANT", "1").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


class DoubleClapGate:
    """Require two clap/snap impulses; ignore singles and typing bursts.

    Flow:
    - 1st impulse → armed (never fires alone; expires after ``window_s``)
    - 2nd within window (≥ ``min_gap_s``) → pending fire after ``settle_s``
    - Extra impulse before settle → reject (keyboard / chassis chatter)
    - ``poll(now)`` emits ``fire`` once settle elapses cleanly
    """

    def __init__(
        self,
        *,
        window_s: float = 0.75,
        settle_s: float = 0.18,
        min_gap_s: float = 0.12,
        cooldown_s: float = 2.8,
    ):
        self.window_s = window_s
        self.settle_s = settle_s
        self.min_gap_s = min_gap_s
        self.cooldown_s = cooldown_s
        self._count = 0
        self._first_t = 0.0
        self._last_impulse = 0.0
        self._fire_at = 0.0
        self._last_fire = 0.0

    def reset(self) -> None:
        self._count = 0
        self._first_t = 0.0
        self._last_impulse = 0.0
        self._fire_at = 0.0

    def note_impulse(self, now: float) -> str:
        """Return ``armed``, ``pending``, ``reject_burst``, or ``cooldown``."""
        if now - self._last_fire < self.cooldown_s:
            return "cooldown"
        # Expire a lone first clap.
        if self._count == 1 and (now - self._first_t) > self.window_s:
            self.reset()
        if self._count > 0 and (now - self._last_impulse) < self.min_gap_s:
            # Same impulse ringing / mic bounce — do not count.
            return "armed" if self._count == 1 else "pending"
        if self._count == 0:
            self._count = 1
            self._first_t = now
            self._last_impulse = now
            return "armed"
        if self._count == 1:
            if (now - self._first_t) > self.window_s:
                # Late second = new first.
                self._count = 1
                self._first_t = now
                self._last_impulse = now
                self._fire_at = 0.0
                return "armed"
            self._count = 2
            self._last_impulse = now
            self._fire_at = now + self.settle_s
            return "pending"
        # Already pending a double — another tap is chatter.
        self.reset()
        return "reject_burst"

    def poll(self, now: float) -> str:
        """Return ``fire``, ``expired``, or ``none``."""
        if self._count == 1 and (now - self._first_t) > self.window_s:
            self.reset()
            return "expired"
        if self._count == 2 and self._fire_at > 0.0 and now >= self._fire_at:
            self.reset()
            self._last_fire = now
            return "fire"
        return "none"


def frame_impulse_features(samples: list[int]) -> tuple[float, float, float]:
    """Return ``(hp_rms, crest, zcr)`` for one PCM frame.

    Snaps/claps are impulsive: high crest factor and high zero-crossing rate
    after a simple differentiator (high-pass). Voiced speech is usually lower
    on both once the slow envelope is removed.
    """
    if len(samples) < 3:
        return 0.0, 0.0, 0.0
    prev = samples[0]
    acc = 0.0
    peak_abs = 0.0
    crossings = 0
    prev_diff = 0
    for sample in samples[1:]:
        diff = sample - prev
        prev = sample
        acc += diff * diff
        ad = abs(diff)
        if ad > peak_abs:
            peak_abs = ad
        if prev_diff != 0 and (diff > 0) != (prev_diff > 0):
            crossings += 1
        if diff != 0:
            prev_diff = diff
    n = len(samples) - 1
    hp_rms = min(1.0, math.sqrt(acc / n) / 32768.0)
    crest = (peak_abs / max(math.sqrt(acc / n), 1.0)) if acc > 0 else 0.0
    zcr = crossings / n
    return hp_rms, crest, zcr


def highpass_rms(samples: list[int]) -> float:
    hp, _crest, _zcr = frame_impulse_features(samples)
    return hp


def detect_snap_frames(
    frames_hp_rms: list[float],
    *,
    baseline: float,
    abs_threshold: float,
    ratio: float,
    max_spike_frames: int = 3,
    frames_crest: list[float] | None = None,
    frames_zcr: list[float] | None = None,
) -> bool:
    """Return True if the recent high-pass RMS series looks like a snap/clap.

    ``frames_*`` lists are newest-last. A snap/clap must be:
    - well above a quiet ambient baseline
    - preceded by near-silence (not mid-speech)
    - a sharp attack (not a gradual shout)
    - short (tens of ms), then back down quickly
    """
    if len(frames_hp_rms) < 6:
        return False
    peak = max(frames_hp_rms)
    floor = max(baseline, 1e-4)
    # Any busy room / loud speech baseline → refuse. Snap needs a quiet pad.
    if floor >= 0.008:
        return False
    need = max(abs_threshold, floor * ratio)
    if floor >= 0.004:
        need = max(need, abs_threshold * 1.75, floor * (ratio * 1.5))
    if peak < need:
        return False

    peak_idx = max(i for i, v in enumerate(frames_hp_rms) if v == peak)
    # Peak must be in the newest ~50ms so we fire on the attack.
    if peak_idx < len(frames_hp_rms) - 5:
        return False

    # Pre-peak context must be quiet (rejects plosives inside ongoing speech).
    pre = frames_hp_rms[max(0, peak_idx - 5) : peak_idx]
    if not pre:
        return False
    pre_peak = max(pre)
    pre_mean = sum(pre) / len(pre)
    if pre_mean > max(floor * 2.2, abs_threshold * 0.28):
        return False
    if pre_peak > max(floor * 3.5, abs_threshold * 0.45):
        return False
    # Sharp attack vs the immediate past (voice rises more gradually).
    attack_ref = max(pre[-1], floor * 1.5, 1e-4)
    if peak < attack_ref * 4.5:
        return False

    hot_floor = max(abs_threshold * 0.55, floor * (ratio * 0.5))
    left = peak_idx
    while left > 0 and frames_hp_rms[left - 1] >= hot_floor:
        left -= 1
    right = peak_idx
    while right + 1 < len(frames_hp_rms) and frames_hp_rms[right + 1] >= hot_floor:
        right += 1
    width = right - left + 1
    # Finger-snap ≈ 1–3 frames (10–30ms). Clap can be ~3–4. Wider = speech.
    if width < 1 or width > max_spike_frames:
        return False

    # Fast decay: energy after the spike body must collapse (not hang like voice).
    after = frames_hp_rms[right + 1 : right + 4]
    if after:
        if max(after) > max(peak * 0.35, need * 0.3):
            return False
        if sum(after) / len(after) > max(floor * 2.5, abs_threshold * 0.25):
            return False

    # Crest factor of the peak frame (when provided): impulses are peaky;
    # sustained voiced frames after HP are usually flatter.
    if frames_crest is not None and len(frames_crest) == len(frames_hp_rms):
        if frames_crest[peak_idx] < 3.0:
            return False
    # ZCR is advisory only for logging shape; do not hard-reject on it.
    # (Clean snaps can be a single transient with few zero crossings.)
    _ = frames_zcr

    return True


class SnapListener:
    """Background mic monitor → ``on_snap`` callback."""

    def __init__(
        self,
        on_snap: Callable[[], None],
        *,
        should_listen: Callable[[], bool] | None = None,
        parec: str = "parec",
    ):
        self.on_snap = on_snap
        self.should_listen = should_listen or (lambda: True)
        self.parec = parec
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._proc: subprocess.Popen[bytes] | None = None
        # Stricter defaults: prefer missed snaps over false voice triggers.
        self._abs_threshold = _env_float("VAANI_SNAP_THRESHOLD", 0.07)
        self._ratio = _env_float("VAANI_SNAP_RATIO", 9.0)
        self._gate = DoubleClapGate(
            window_s=_env_float("VAANI_SNAP_DOUBLE_WINDOW_S", 0.75),
            settle_s=_env_float("VAANI_SNAP_SETTLE_S", 0.18),
            min_gap_s=_env_float("VAANI_SNAP_MIN_GAP_S", 0.12),
            cooldown_s=_env_float("VAANI_SNAP_COOLDOWN", 2.8),
        )
        self._baseline = 0.002

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="vaani-snap-listener", daemon=True
        )
        self._thread.start()
        logger.info(
            "event=snap_listener_start threshold=%.3f ratio=%.1f double_window=%.2f",
            self._abs_threshold,
            self._ratio,
            self._gate.window_s,
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
            logger.warning("event=snap_parec_failed detail=%s", type(exc).__name__)
            return None
        self._proc = proc
        return proc

    def _fire(self) -> None:
        logger.info("event=snap_detected")
        try:
            self.on_snap()
        except Exception:
            logger.exception("snap callback failed")

    def _run(self) -> None:
        recent_hp: list[float] = []
        recent_crest: list[float] = []
        recent_zcr: list[float] = []
        buf = b""
        while not self._stop.is_set():
            if not self.should_listen():
                self._kill_proc()
                recent_hp.clear()
                recent_crest.clear()
                recent_zcr.clear()
                self._gate.reset()
                buf = b""
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
                recent_crest.clear()
                recent_zcr.clear()
                self._gate.reset()
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
                hp, crest, zcr = frame_impulse_features(samples)
                now = time.monotonic()
                # Slow ambient baseline (ignore spikes).
                if hp < self._baseline * 2.2:
                    self._baseline = (0.96 * self._baseline) + (0.04 * max(hp, 1e-5))
                recent_hp.append(hp)
                recent_crest.append(crest)
                recent_zcr.append(zcr)
                if len(recent_hp) > 14:
                    recent_hp.pop(0)
                    recent_crest.pop(0)
                    recent_zcr.pop(0)

                candidate = detect_snap_frames(
                    recent_hp,
                    baseline=self._baseline,
                    abs_threshold=self._abs_threshold,
                    ratio=self._ratio,
                    frames_crest=recent_crest,
                    frames_zcr=recent_zcr,
                )
                if candidate:
                    recent_hp.clear()
                    recent_crest.clear()
                    recent_zcr.clear()
                    decision = self._gate.note_impulse(now)
                    if decision == "armed":
                        logger.info("event=snap_armed waiting_for_second")
                    elif decision == "pending":
                        logger.info("event=snap_pending settle_for_double")
                    elif decision == "reject_burst":
                        logger.info("event=snap_rejected reason=tap_burst")

                polled = self._gate.poll(now)
                if polled == "fire":
                    self._fire()
                    try:
                        if proc.stdout is not None:
                            proc.stdout.read(_FRAME_BYTES * 10)
                    except Exception:
                        pass
                    recent_hp.clear()
                    recent_crest.clear()
                    recent_zcr.clear()
