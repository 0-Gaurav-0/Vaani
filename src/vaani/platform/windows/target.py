"""Foreground-window focus probe for Windows paste safety."""
from __future__ import annotations

from typing import Any, Callable

from ..protocol import FocusSnapshot


def _default_foreground_hwnd() -> int:
    import ctypes

    return int(ctypes.windll.user32.GetForegroundWindow())


class WindowsTargetProbe:
    """Snapshot the foreground hwnd so paste can abort if focus moves."""

    def __init__(self, foreground: Callable[[], int] | None = None):
        self._foreground = foreground or _default_foreground_hwnd

    def snapshot(self) -> FocusSnapshot | None:
        try:
            hwnd = int(self._foreground())
        except Exception:
            return None
        if hwnd <= 0:
            return None
        return FocusSnapshot(token=str(hwnd))

    def unchanged(self, before: FocusSnapshot | Any) -> bool:
        if before is None:
            return False
        token = getattr(before, "token", before)
        current = self.snapshot()
        if current is None:
            return False
        return str(token) == current.token
