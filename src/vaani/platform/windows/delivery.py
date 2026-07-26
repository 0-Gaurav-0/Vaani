"""Clipboard + Ctrl+V paste delivery for Windows."""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

from ...delivery import DeliveryStatus


def _pyperclip_set(text: str) -> None:
    import pyperclip

    pyperclip.copy(text)


def _pyperclip_get() -> str | None:
    import pyperclip

    value = pyperclip.paste()
    return value if isinstance(value, str) else None


def _pynput_ctrl_v() -> None:
    from pynput.keyboard import Controller, Key

    keyboard = Controller()
    keyboard.press(Key.ctrl)
    keyboard.press("v")
    keyboard.release("v")
    keyboard.release(Key.ctrl)


class WindowsDelivery:
    """Set the clipboard, verify readback, then synthesize Ctrl+V when focus is stable."""

    def __init__(
        self,
        *,
        target: Any | None = None,
        set_clipboard: Callable[[str], None] | None = None,
        get_clipboard: Callable[[], str | None] | None = None,
        paste: Callable[[], None] | None = None,
        readback_timeout: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.target = target
        self._set_clipboard = set_clipboard or _pyperclip_set
        self._get_clipboard = get_clipboard or _pyperclip_get
        self._paste = paste or _pynput_ctrl_v
        self.readback_timeout = readback_timeout
        self._sleep = sleep
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()

    def shutdown(self) -> None:
        self.cancel()

    def _same_target(self, snapshot: Any) -> bool:
        if self.target is None or snapshot is None:
            return False
        try:
            check = getattr(self.target, "unchanged", None)
            return bool(check(snapshot)) if check else False
        except Exception:
            return False

    def deliver(self, text: str, *, snapshot: Any | None = None) -> DeliveryStatus:
        if self._cancelled.is_set() or not isinstance(text, str) or not text:
            return DeliveryStatus.FAILED
        pre_ok = snapshot is None or self.target is None or self._same_target(snapshot)
        try:
            self._set_clipboard(text)
        except Exception:
            return DeliveryStatus.FAILED
        deadline = time.monotonic() + self.readback_timeout
        verified = False
        while time.monotonic() < deadline and not self._cancelled.is_set():
            try:
                if self._get_clipboard() == text:
                    verified = True
                    break
            except Exception:
                pass
            self._sleep(0.01)
        if not verified or self._cancelled.is_set():
            return DeliveryStatus.FAILED
        if not pre_ok or (snapshot is not None and not self._same_target(snapshot)):
            return DeliveryStatus.CLIPBOARD_ONLY
        try:
            self._paste()
        except Exception:
            return DeliveryStatus.CLIPBOARD_ONLY
        return DeliveryStatus.PASTE_DISPATCHED
