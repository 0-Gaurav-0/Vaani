"""Vision-model adapter for guide-mode screen pointers."""
from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, Protocol

from vaani.intent.schema import OverlayOp, ScreenFrame

from .point_parse import parse_vision_reply

GUIDE_POINT_PROMPT = """You guide a user by looking at screenshots.
Screenshot coordinates are pixels with (0,0) at the top-left of each screenshot.
Answer casually in one spoken line of 80 characters or fewer; do not use markdown.
Finish your entire reply with exactly one tag: [POINT:x,y:label:screenN] when a
target is visible, or [POINT:none] when it is not. Do not emit any other tags."""

_VISION_FAILURE = "I couldn't inspect the screen."


class VisionChatClient(Protocol):
    def vision_chat(self, question: str, frames: Sequence[Any]) -> str | None: ...


def guide_point(
    question: str,
    frames: Sequence[ScreenFrame] | Sequence[Any],
    *,
    client: VisionChatClient,
) -> tuple[str, tuple[OverlayOp, ...]]:
    """Ask a vision client where to point, returning spoken text and overlay ops."""
    if not frames:
        return "I need a screen to point at.", ()
    try:
        reply = client.vision_chat(question, frames)
    except Exception:
        return _VISION_FAILURE, ()
    if not isinstance(reply, str) or not reply.strip():
        return _VISION_FAILURE, ()
    return parse_vision_reply(reply)


class _KeyedVisionClient:
    def __init__(self, groq: Any, key_provider: Callable[[], str | None]) -> None:
        self._groq = groq
        self._key_provider = key_provider

    def vision_chat(self, question: str, frames: Sequence[Any]) -> str | None:
        key = self._key_provider()
        if not key:
            return None
        return self._groq.vision_chat(
            question, frames, key, system=GUIDE_POINT_PROMPT
        )


def make_guide_brain(
    groq: Any, key_provider: Callable[[], str | None]
) -> Callable[[str, Sequence[ScreenFrame] | Sequence[Any]], tuple[str, tuple[OverlayOp, ...]]]:
    """Bind a Groq client and key provider into the guide-point callable."""
    client = _KeyedVisionClient(groq, key_provider)

    def guide(
        question: str, frames: Sequence[ScreenFrame] | Sequence[Any]
    ) -> tuple[str, tuple[OverlayOp, ...]]:
        return guide_point(question, frames, client=client)

    return guide
