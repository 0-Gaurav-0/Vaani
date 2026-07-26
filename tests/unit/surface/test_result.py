"""Tests for shared Result / assistant result surface (T1.4)."""
from __future__ import annotations

from vaani.intent.schema import PendingAction, Result, RiskClass, Status
from vaani.surface.result import (
    format_assistant_text,
    format_result_message,
    make_assistant_sink,
    show_result,
)


class _Notifier:
    def __init__(self) -> None:
        self.notifications: list[tuple[str, str]] = []

    def notify(self, category: str, message: str = "") -> None:
        self.notifications.append((category, message))


def test_format_assistant_text_trims_and_defaults() -> None:
    assert format_assistant_text("  hello  ") == "hello"
    assert format_assistant_text("") == "Assistant returned no output."
    assert format_assistant_text("x" * 200) == "x" * 160


def test_format_result_message_ok_and_confirm() -> None:
    ok = Result(status=Status.OK, summary="Muted", detail="Volume set to 0")
    assert format_result_message(ok) == "Muted"

    pending = PendingAction(
        id="a1",
        verb="system.volume.set",
        slots={"level": 30},
        materialized=("osascript", "-e", "set volume 30"),
        risk=RiskClass.R2,
        expires_at=0.0,
    )
    confirm = Result(
        status=Status.NEEDS_CONFIRM,
        summary="Set volume to 30%",
        pending=pending,
    )
    message = format_result_message(confirm)
    assert "Set volume to 30%" in message
    assert "osascript" in message


def test_make_assistant_sink_notifies() -> None:
    notifier = _Notifier()
    sink = make_assistant_sink(notifier)
    sink("  assistant says hi  ")
    assert notifier.notifications == [("paste", "assistant says hi")]


def test_show_result_uses_formatted_message() -> None:
    notifier = _Notifier()
    show_result(notifier, Result(status=Status.FAILED, summary="Could not lock"))
    assert notifier.notifications == [("paste", "Could not lock")]
