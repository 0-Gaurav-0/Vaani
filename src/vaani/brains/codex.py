"""Codex CLI brain adapter — real subprocess exec via CodexRunner."""
from __future__ import annotations

import secrets
import threading
from collections.abc import Callable, Sequence

from typing import Any

from vaani.brains.protocol import BrainResult, ToolSpec


class CodexBrain:
    """BrainProtocol backed by the existing ephemeral Codex CLI runner."""

    def __init__(self, runner: Any | None = None) -> None:
        if runner is None:
            from vaani.codex import CodexRunner

            runner = CodexRunner()
        self._runner = runner

    @property
    def name(self) -> str:
        return "codex"

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
        # Catalog is accepted so callers share one contract; Codex CLI does not
        # yet consume tool JSON (T7.2 wires verb-backed tool loops).
        _ = tools
        if on_progress is not None:
            on_progress("codex: starting")
        if cancel is not None and cancel.is_set():
            return BrainResult(
                text="",
                cancelled=True,
                session_id=session_id or secrets.token_hex(8),
            )
        result = self._runner.run(prompt, timeout=timeout)
        if cancel is not None and cancel.is_set():
            return BrainResult(
                text=result.stdout,
                stderr=result.stderr,
                cancelled=True,
                timed_out=result.timed_out,
                session_id=session_id or secrets.token_hex(8),
            )
        if on_progress is not None and not result.timed_out and not result.cancelled:
            on_progress("codex: complete")
        return BrainResult(
            text=result.stdout,
            stderr=result.stderr,
            timed_out=result.timed_out,
            cancelled=result.cancelled,
            session_id=session_id or secrets.token_hex(8),
        )

    def cancel(self) -> None:
        self._runner.cancel()
