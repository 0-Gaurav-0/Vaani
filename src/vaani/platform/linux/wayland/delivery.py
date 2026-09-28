"""Wayland text delivery: clipboard always, synthetic paste best-effort."""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

from ....delivery import DeliveryStatus
from .clipboard import WlClipboard, WtypePaster


class WaylandClipboardDelivery:
    """No target/focus tracking exists on Wayland for an unprivileged client,
    so unlike the X11/macOS/Windows backends this never claims to verify
    "did the focused window change" — it always attempts a paste and
    downgrades to CLIPBOARD_ONLY on any failure. That naturally matches
    GNOME/KDE (wtype absent or rejected -> always clipboard-only) against
    Sway/Hyprland (wtype works -> paste-dispatched) without a new
    DeliveryStatus value.
    """

    def __init__(self, clipboard: Any | None = None, paster: Any | None = None, *,
                 readback_timeout: float = 2.0, sleep: Callable[[float], None] = time.sleep):
        self.clipboard = clipboard or WlClipboard()
        self.paster = paster or WtypePaster()
        self.readback_timeout, self._sleep = readback_timeout, sleep
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        self._cancelled.set()

    def shutdown(self) -> None:
        self.cancel()

    def deliver(self, text: str, snapshot: Any | None = None) -> DeliveryStatus:
        if self._cancelled.is_set() or not isinstance(text, str) or not text:
            return DeliveryStatus.FAILED
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
        try:
            self.paster.paste()
        except Exception:
            return DeliveryStatus.CLIPBOARD_ONLY
        return DeliveryStatus.PASTE_DISPATCHED
