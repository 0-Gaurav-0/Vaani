"""Consistent Result / assistant-output rendering for feedback adapters."""
from __future__ import annotations

from typing import Callable, Protocol

from vaani.intent.schema import Result, Status

NOTIFY_MAX = 160


class Notifier(Protocol):
    def notify(self, category: str, message: str = "") -> None: ...


def format_assistant_text(text: str) -> str:
    """Normalize free-form assistant/Codex output for notify surfaces."""
    message = (text or "").strip() or "Assistant returned no output."
    return message[:NOTIFY_MAX]


def format_result_message(result: Result) -> str:
    """Single-line notify text for a verb ``Result``."""
    summary = (result.summary or "").strip()
    detail = (result.detail or "").strip()

    if result.status is Status.NEEDS_CONFIRM and result.pending is not None:
        argv = " ".join(result.pending.materialized).strip()
        parts = [part for part in (summary, argv) if part]
        if not parts:
            message = "Confirm this action?"
        elif len(parts) == 1:
            message = parts[0]
        elif argv in summary:
            message = summary
        else:
            message = f"{summary}: {argv}"
        return message[:NOTIFY_MAX]

    if result.status is Status.DRY_RUN:
        message = summary or detail or "Dry run — nothing executed."
        if not message.casefold().startswith("dry"):
            message = f"Dry run: {message}"
        return message[:NOTIFY_MAX]

    if result.status is Status.REFUSED:
        return (summary or "Refused.")[:NOTIFY_MAX]

    if result.status is Status.UNSUPPORTED:
        return (summary or "Unsupported on this platform.")[:NOTIFY_MAX]

    if result.status is Status.FAILED:
        return (summary or detail or "Failed.")[:NOTIFY_MAX]

    if result.status is Status.PARTIAL:
        return (summary or detail or "Partial success.")[:NOTIFY_MAX]

    return (summary or detail or "Done.")[:NOTIFY_MAX]


def show_assistant_text(
    notifier: Notifier, text: str, *, category: str = "paste"
) -> None:
    notifier.notify(category, format_assistant_text(text))


def show_result(
    notifier: Notifier, result: Result, *, category: str = "paste"
) -> None:
    notifier.notify(category, format_result_message(result))


def make_assistant_sink(notifier: Notifier) -> Callable[[str], None]:
    """Build a ``ResultWindow``-compatible sink backed by ``notifier``."""

    def sink(text: str) -> None:
        show_assistant_text(notifier, text)

    return sink
