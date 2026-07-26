"""User-facing output surfaces (result window, pill, overlay)."""

from .result import (
    format_assistant_text,
    format_result_message,
    make_assistant_sink,
    show_assistant_text,
    show_result,
)

__all__ = [
    "format_assistant_text",
    "format_result_message",
    "make_assistant_sink",
    "show_assistant_text",
    "show_result",
]
