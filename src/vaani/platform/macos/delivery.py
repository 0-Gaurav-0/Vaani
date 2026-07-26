"""Clipboard + Cmd+V paste delivery for macOS."""
from __future__ import annotations

import subprocess
import threading
import time
from typing import Any, Callable

from ...delivery import DeliveryStatus


class PyperclipClipboard:
    """Thin wrapper so tests can inject a fake clipboard."""

    def set_text(self, text: str) -> None:
        import pyperclip

        pyperclip.copy(text)

    def read_text(self) -> str | None:
        import pyperclip

        try:
            value = pyperclip.paste()
        except Exception:
            return None
        return value if isinstance(value, str) else None


class CmdVPaster:
    """Synthesize ⌘V via pynput, with an osascript fallback."""

    def __init__(
        self,
        *,
        paste_fn: Callable[[], None] | None = None,
        runner: Callable[..., Any] | None = None,
    ):
        self._paste_fn = paste_fn
        self._runner = runner or subprocess.run

    def paste(self) -> None:
        if self._paste_fn is not None:
            self._paste_fn()
            return
        try:
            self._paste_pynput()
            return
        except Exception:
            pass
        self._paste_osascript()

    def _paste_pynput(self) -> None:
        from pynput.keyboard import Controller, Key

        controller = Controller()
        controller.press(Key.cmd)
        try:
            controller.press("v")
            controller.release("v")
        finally:
            controller.release(Key.cmd)

    def _paste_osascript(self) -> None:
        result = self._runner(
            [
                "osascript",
                "-e",
                'tell application "System Events" to keystroke "v" using command down',
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if getattr(result, "returncode", 1) != 0:
            raise RuntimeError("osascript paste failed")

    dispatch = paste


class MacClipboardDelivery:
    """Set clipboard, verify readback, paste only if focus is unchanged."""

    def __init__(
        self,
        clipboard: Any | None = None,
        target: Any | None = None,
        paster: Any | None = None,
        *,
        readback_timeout: float = 2.0,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.clipboard = clipboard or PyperclipClipboard()
        self.target = target
        self.paster = paster or CmdVPaster()
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
        pre_target_ok = snapshot is None or self.target is None or self._same_target(snapshot)
        try:
            self.clipboard.set_text(text)
        except Exception:
            return DeliveryStatus.FAILED
        deadline = time.monotonic() + self.readback_timeout
        verified = False
        while time.monotonic() < deadline and not self._cancelled.is_set():
            try:
                if self.clipboard.read_text() == text:
                    verified = True
                    break
            except Exception:
                pass
            self._sleep(0.01)
        if not verified or self._cancelled.is_set():
            return DeliveryStatus.FAILED
        if not pre_target_ok or (snapshot is not None and not self._same_target(snapshot)):
            return DeliveryStatus.CLIPBOARD_ONLY
        try:
            self.paster.paste()
        except Exception:
            return DeliveryStatus.CLIPBOARD_ONLY
        return DeliveryStatus.PASTE_DISPATCHED


ClipboardDelivery = MacClipboardDelivery
Delivery = MacClipboardDelivery
