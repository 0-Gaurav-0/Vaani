"""Best-effort Linux feedback (re-exported for historical imports)."""
from __future__ import annotations

from .platform.linux.feedback import LinuxFeedback as Feedback

FeedbackPlayer = Feedback


def play_feedback(cue: str, *, player: Feedback | None = None) -> bool:
    return (player or Feedback()).play(cue)
