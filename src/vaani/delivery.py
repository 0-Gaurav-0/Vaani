"""Privacy-preserving clipboard and XTEST text delivery."""
from __future__ import annotations

import threading
import time
import subprocess
from enum import Enum
from typing import Any, Callable


class DeliveryStatus(str, Enum):
    PASTE_DISPATCHED = "paste_dispatched"
    CLIPBOARD_ONLY = "clipboard_only"
    FAILED = "failed"


class GtkClipboard:
    """Small adapter; retaining this object retains GTK clipboard ownership."""
    def __init__(self, clipboard: Any | None = None):
        if clipboard is None:
            try:
                from gi.repository import Gdk, Gtk
                clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            except Exception:
                clipboard = None
        self.clipboard = clipboard

    def set_text(self, text: str) -> None:
        if self.clipboard is None:
            subprocess.run(["xclip", "-selection", "clipboard"], input=text, text=True, check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        self.clipboard.set_text(text, -1)
        wait = getattr(self.clipboard, "store", None)
        if wait: wait()

    def read_text(self) -> str | None:
        if self.clipboard is None:
            result = subprocess.run(["xclip", "-selection", "clipboard", "-o"], text=True,
                                    capture_output=True, check=False)
            return result.stdout if result.returncode == 0 else None
        getter = getattr(self.clipboard, "wait_for_text", None)
        return getter() if getter else None


class XTestPaster:
    def __init__(self, display: Any | None = None):
        self.display = display

    def paste(self, *, timeout: float = 0.25, poll: Callable[[], bool] | None = None) -> None:
        if self.display is None:
            from Xlib.display import Display
            self.display = Display()
        from Xlib import X, XK
        from Xlib.ext import xtest
        d = self.display
        if poll is not None:
            deadline = time.monotonic() + timeout
            while not poll():
                if time.monotonic() >= deadline:
                    raise TimeoutError("modifier release timeout")
                time.sleep(0.01)
        # Terminal emulators reserve Ctrl+V; their conventional paste shortcut
        # is Ctrl+Shift+V. Editors and browsers use Ctrl+V.
        shift = False
        try:
            root = d.screen().root
            atom = d.intern_atom("_NET_ACTIVE_WINDOW")
            prop = root.get_full_property(atom, X.AnyPropertyType)
            wid = int(prop.value[0]) if prop is not None and getattr(prop, "value", None) else 0
            window = d.create_resource_object("window", wid)
            classes = " ".join(window.get_wm_class() or ()).lower()
            shift = any(name in classes for name in ("terminal", "gnome-terminal", "konsole", "alacritty", "kitty", "xterm", "tilix", "terminator"))
        except Exception:
            pass
        ctrl = d.keysym_to_keycode(XK.string_to_keysym("Control_L"))
        shift_key = d.keysym_to_keycode(XK.string_to_keysym("Shift_L"))
        v = d.keysym_to_keycode(XK.string_to_keysym("v"))
        ctrl_down = shift_down = False
        try:
            xtest.fake_input(d, X.KeyPress, ctrl); ctrl_down = True
            if shift:
                xtest.fake_input(d, X.KeyPress, shift_key); shift_down = True
            xtest.fake_input(d, X.KeyPress, v)
            xtest.fake_input(d, X.KeyRelease, v)
        finally:
            if shift_down:
                try: xtest.fake_input(d, X.KeyRelease, shift_key)
                except Exception: pass
            if ctrl_down:
                try: xtest.fake_input(d, X.KeyRelease, ctrl)
                except Exception: pass
        d.sync()

    dispatch = paste


class ClipboardDelivery:
    def __init__(self, clipboard: Any | None = None, target: Any | None = None,
                 paster: Any | None = None, *, readback_timeout: float = 2.0,
                 grace: float = 2.0, sleep: Callable[[float], None] = time.sleep):
        self.clipboard = clipboard or GtkClipboard()
        self.target, self.paster = target, paster or XTestPaster()
        self.readback_timeout, self.grace, self._sleep = readback_timeout, grace, sleep
        self._cancelled = threading.Event()
        self._owner = self.clipboard

    def cancel(self) -> None:
        self._cancelled.set()

    def shutdown(self) -> None:
        self.cancel()
        self._owner = None

    def _same_target(self, snapshot: Any) -> bool:
        if self.target is None: return False
        try:
            check = getattr(self.target, "unchanged", None)
            return bool(check(snapshot)) if check else False
        except Exception: return False

    def deliver(self, text: str, snapshot: Any | None = None) -> DeliveryStatus:
        if self._cancelled.is_set() or not isinstance(text, str) or not text:
            return DeliveryStatus.FAILED
        # Safety probe immediately before replacing the selection.
        pre_target_ok = snapshot is None or self.target is None or self._same_target(snapshot)
        try: self.clipboard.set_text(text)
        except Exception: return DeliveryStatus.FAILED
        deadline = time.monotonic() + self.readback_timeout
        verified = False
        while time.monotonic() < deadline and not self._cancelled.is_set():
            try:
                if self.clipboard.read_text() == text: verified = True; break
            except Exception: pass
            self._sleep(0.01)
        if not verified or self._cancelled.is_set(): return DeliveryStatus.FAILED
        if not pre_target_ok or (snapshot is not None and not self._same_target(snapshot)):
            return DeliveryStatus.CLIPBOARD_ONLY
        try:
            poll = getattr(self.target, "modifiers_released", None)
            if poll:
                try: self.paster.paste(poll=poll)
                except TypeError: self.paster.paste()
            else: self.paster.paste()
        except Exception: return DeliveryStatus.CLIPBOARD_ONLY
        return DeliveryStatus.PASTE_DISPATCHED


Delivery = ClipboardDelivery

def deliver_text(text: str, *, clipboard: Any | None = None, target: Any | None = None,
                 paster: Any | None = None, snapshot: Any | None = None) -> DeliveryStatus:
    """Convenience seam used by the controller and integration fakes."""
    return ClipboardDelivery(clipboard, target, paster).deliver(text, snapshot)
