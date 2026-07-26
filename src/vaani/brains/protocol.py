"""Brain adapter protocol — pluggable Codex / Claude / Cursor backends (T7.1)."""
from __future__ import annotations

import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ToolSpec:
    """One registry verb exposed to a brain as a callable tool."""

    name: str
    title: str
    slots: Mapping[str, str]
    risk: str
    rung: int


@dataclass(frozen=True)
class ToolCall:
    """Brain-requested verb invocation (never raw shell)."""

    name: str
    slots: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BrainResult:
    """Normalized brain turn outcome."""

    text: str
    tool_calls: tuple[ToolCall, ...] = ()
    timed_out: bool = False
    cancelled: bool = False
    session_id: str | None = None
    stderr: str = ""


class BrainProtocol(Protocol):
    """Adapter interface for rung-6 agent backends.

    Implementations receive the verb catalog as ``tools`` and must not invent
    shell. Thin stubs are allowed until real exec is wired; the signature is
    load-bearing.
    """

    @property
    def name(self) -> str: ...

    def run(
        self,
        prompt: str,
        *,
        tools: Sequence[ToolSpec],
        session_id: str | None = None,
        cancel: threading.Event | None = None,
        on_progress: Callable[[str], None] | None = None,
        timeout: float | None = None,
    ) -> BrainResult: ...

    def cancel(self) -> None: ...
