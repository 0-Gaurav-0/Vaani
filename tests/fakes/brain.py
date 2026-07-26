"""Fake / scripted brains for AgentRunner tests (plan §13.1)."""
from __future__ import annotations

import threading
from collections.abc import Callable, Sequence

from vaani.brains.protocol import BrainResult, ToolCall, ToolSpec


class FakeBrain:
    """Raises by default — invariant: unexpected brain calls must fail loudly."""

    def __init__(self, name: str = "fake") -> None:
        self._name = name
        self.calls: list[dict[object, object]] = []

    @property
    def name(self) -> str:
        return self._name

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
        self.calls.append(
            {
                "prompt": prompt,
                "tools": tuple(t.name for t in tools),
                "session_id": session_id,
                "timeout": timeout,
            }
        )
        raise AssertionError(
            "FakeBrain.raise_by_default: unexpected brain call "
            f"(prompt={prompt!r})"
        )

    def cancel(self) -> None:
        return None


class ScriptedBrain:
    """Configurable brain that returns a scripted BrainResult."""

    def __init__(
        self,
        name: str = "codex",
        *,
        text: str = "ok",
        tool_calls: Sequence[ToolCall] = (),
        timed_out: bool = False,
        cancelled: bool = False,
        delay: float = 0.0,
        redact_probe: str = "",
    ) -> None:
        self._name = name
        self.text = text
        self.tool_calls = tuple(tool_calls)
        self.timed_out = timed_out
        self.cancelled = cancelled
        self.delay = delay
        self.redact_probe = redact_probe
        self.calls: list[dict[object, object]] = []
        self._cancel = threading.Event()

    @property
    def name(self) -> str:
        return self._name

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
        self.calls.append(
            {
                "prompt": prompt,
                "tools": tuple(t.name for t in tools),
                "session_id": session_id,
                "timeout": timeout,
            }
        )
        if on_progress is not None:
            on_progress(f"{self._name}: starting")
        if self.delay:
            import time

            time.sleep(self.delay)
        if (cancel is not None and cancel.is_set()) or self._cancel.is_set():
            return BrainResult(
                text="",
                cancelled=True,
                session_id=session_id or "scripted",
            )
        stderr = self.redact_probe
        return BrainResult(
            text=self.text,
            tool_calls=self.tool_calls,
            timed_out=self.timed_out,
            cancelled=self.cancelled,
            session_id=session_id or "scripted",
            stderr=stderr,
        )

    def cancel(self) -> None:
        self._cancel.set()
