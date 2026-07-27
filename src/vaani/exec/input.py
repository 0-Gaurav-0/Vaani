"""Rung-7 input synthesis helpers (T5.2).

OS leaves implement :class:`~vaani.platform.protocol.InputSynth`. The shared
caveat string lives in the computer-use pack (L3); this L5 module re-exports it
and provides test doubles.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from vaani.intent.schema import Result, Status
from vaani.verbs.packs.computer_use import TYPED_CAVEAT, with_caveat

__all__ = [
    "TYPED_CAVEAT",
    "FakeInputSynth",
    "UnsupportedInputSynth",
    "with_caveat",
]


@dataclass
class FakeInputSynth:
    """Test double that records keystroke calls without touching the OS."""

    calls: list[tuple[str, Any]] = field(default_factory=list)
    type_result: Result | None = None
    hotkey_result: Result | None = None

    def type_text(self, text: str) -> Result:
        self.calls.append(("type_text", text))
        if self.type_result is not None:
            return self.type_result
        return Result(
            status=Status.OK,
            summary="Typed text",
            detail="",
            evidence=("type_text", text),
            rung=7,
        )

    def hotkey(self, *keys: str) -> Result:
        self.calls.append(("hotkey", tuple(keys)))
        if self.hotkey_result is not None:
            return self.hotkey_result
        return Result(
            status=Status.OK,
            summary="Sent hotkey",
            detail="",
            evidence=("hotkey", *keys),
            rung=7,
        )

    def click(self, x: float, y: float) -> Result:
        self.calls.append(("click", (x, y)))
        return Result(
            status=Status.OK,
            summary="Clicked",
            detail="",
            evidence=("click", f"{x},{y}"),
            rung=7,
        )


@dataclass(frozen=True)
class UnsupportedInputSynth:
    """Honest stub when the OS cannot inject keystrokes."""

    reason: str = "input synthesis unsupported on this platform"

    def type_text(self, text: str) -> Result:
        _ = text
        return Result(
            status=Status.UNSUPPORTED,
            summary="Keystrokes unsupported",
            detail=self.reason,
            evidence=("input.type_text",),
            rung=7,
        )

    def hotkey(self, *keys: str) -> Result:
        _ = keys
        return Result(
            status=Status.UNSUPPORTED,
            summary="Keystrokes unsupported",
            detail=self.reason,
            evidence=("input.hotkey",),
            rung=7,
        )

    def click(self, x: float, y: float) -> Result:
        _ = (x, y)
        return Result(
            status=Status.UNSUPPORTED,
            summary="Click unsupported",
            detail=self.reason,
            evidence=("input.click",),
            rung=7,
        )
