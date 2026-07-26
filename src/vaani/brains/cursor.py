"""Cursor brain adapter — thin stub until real exec is wired (T7.1)."""
from __future__ import annotations

import secrets
import threading
from collections.abc import Callable, Sequence

from vaani.brains.protocol import BrainResult, ToolSpec


class CursorBrain:
    """Stub BrainProtocol for Cursor. Interface is real; exec is deferred."""

    @property
    def name(self) -> str:
        return "cursor"

    def run(
        self,
        prompt: str,
        *,
        tools: Sequence[ToolSpec],
        session_id: str | None = None,
        cancel: threading.Event | None = None,
        on_progress: Callable[[str], None] | None = None,
        timeout: float | None = None,
    ) -> BrainResult:
        _ = timeout
        if on_progress is not None:
            on_progress("cursor: starting")
        if cancel is not None and cancel.is_set():
            return BrainResult(
                text="",
                cancelled=True,
                session_id=session_id or secrets.token_hex(8),
            )
        tool_names = ", ".join(t.name for t in tools[:12])
        more = "" if len(tools) <= 12 else f" (+{len(tools) - 12} more)"
        text = (
            f"Cursor brain stub (not wired). Prompt: {prompt.strip() or '(empty)'}. "
            f"Tools available: {tool_names}{more}."
        )
        if on_progress is not None:
            on_progress("cursor: complete")
        return BrainResult(
            text=text,
            session_id=session_id or secrets.token_hex(8),
        )

    def cancel(self) -> None:
        return None
