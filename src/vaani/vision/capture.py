"""Capture orchestration and model-ready labels for screen frames."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from vaani.intent.schema import ScreenFrame


class ScreenCaptureError(RuntimeError):
    """Raised when an in-memory display capture cannot be produced."""


class ScreenCapture(Protocol):
    """Platform capture leaf consumed by guide mode."""

    def capture(self, *, display_index: int = 0) -> ScreenFrame: ...


@dataclass(frozen=True)
class LabeledFrame:
    """A frame paired with unambiguous context for a vision model."""

    label: str
    image: ScreenFrame


def label_frames(frames: Sequence[ScreenFrame]) -> tuple[LabeledFrame, ...]:
    """Give every supplied screenshot a stable, human-readable image label."""
    total = len(frames)
    return tuple(
        LabeledFrame(
            label=(
                f"screen {position} of {total} — "
                f"display {frame.display_index + 1} ({frame.width}x{frame.height})"
            ),
            image=frame,
        )
        for position, frame in enumerate(frames, start=1)
    )


def capture_frames(
    capture: ScreenCapture, *, display_indexes: Sequence[int] = (0,)
) -> tuple[LabeledFrame, ...]:
    """Capture requested displays in memory and label them for vision chat."""
    return label_frames(
        tuple(capture.capture(display_index=index) for index in display_indexes)
    )
