"""Shape mic amplitude into a bouncy multi-bar waveform for the recording pill."""
from __future__ import annotations

import math
import time
from collections import deque


def boost_level(level: float) -> float:
    """Map real mic RMS (often 0.005–0.15 while talking) into a punchy 0..1 range.

    PortAudio levels on macOS are typically tiny; without a steep curve the bars
    all sit at the minimum height and look frozen.
    """
    x = max(0.0, min(1.0, float(level)))
    # Floor: treat near-silence as true silence for the "idle breath" path.
    if x < 0.003:
        return 0.0
    # Aggressive expand — 0.01 → ~0.35, 0.05 → ~0.75, 0.15 → ~1.0
    return max(0.0, min(1.0, math.pow(x * 22.0, 0.48)))


class WaveformBuffer:
    """Keep recent levels and project them onto N display bars."""

    def __init__(self, bars: int = 15, history: int = 36):
        self.bars = max(3, int(bars))
        self._levels: deque[float] = deque(maxlen=history)
        self._display = [0.12] * self.bars
        self._t0 = time.monotonic()

    def push(self, level: float) -> None:
        self._levels.append(boost_level(level))

    def bars_now(self) -> list[float]:
        n = self.bars
        t = time.monotonic() - self._t0
        hist = list(self._levels)
        peak = max(hist) if hist else 0.0

        # No useful mic yet — soft idle shimmer (recording feel without fake loudness).
        if peak < 0.02:
            return [
                0.10 + 0.08 * abs(math.sin(t * 4.0 + i * 0.85)) for i in range(n)
            ]

        # Latest samples across the bar row (left = older).
        samples: list[float] = []
        for i in range(n):
            idx = int(round(i * (len(hist) - 1) / max(1, n - 1)))
            samples.append(hist[idx])

        out: list[float] = []
        for i, sample in enumerate(samples):
            # Diamond envelope like the reference pill.
            center = 1.0 - abs((i / max(1, n - 1)) * 2.0 - 1.0)
            envelope = 0.40 + 0.60 * (center**0.75)
            left = samples[i - 1] if i else sample
            right = samples[i + 1] if i + 1 < n else sample
            blended = 0.18 * left + 0.64 * sample + 0.18 * right
            # Per-bar bounce so neighboring bars don't move in lockstep.
            bounce = 0.22 * peak * abs(math.sin(t * 14.0 + i * 1.7 + peak * 9.0))
            target = max(0.08, min(1.0, (blended * envelope + bounce) * (0.65 + 0.55 * peak)))
            prev = self._display[i]
            # Fast attack, medium release — reads as "bouncy".
            alpha = 0.72 if target > prev else 0.38
            eased = prev + (target - prev) * alpha
            out.append(max(0.08, min(1.0, eased)))
        self._display = out
        return out
