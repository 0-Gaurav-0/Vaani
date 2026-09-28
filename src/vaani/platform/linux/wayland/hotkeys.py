"""Global hotkeys via the XDG Desktop Portal GlobalShortcuts interface.

GNOME 45+ and KDE Plasma 6+ ship this portal. Unlike X11's silent
``XGrabKey``, binding a shortcut here surfaces a one-time system dialog for
the user to assign each chord's key combo — that's a portal design
requirement (see ``portal.py`` module docstring), not a bug.

Verified against a live D-Bus session bus (see ``portal.py``); the
GlobalShortcuts interface itself is implemented strictly to spec but this
dev box's portal doesn't have it installed, so the full bind/Activated flow
needs a real pass on a GNOME/KDE Wayland session before production use.
"""
from __future__ import annotations

import logging
import queue
import threading
from typing import Any, Callable

from jeepney import HeaderFields
from jeepney.io.threading import DBusRouter, open_dbus_connection

from .portal import GlobalShortcutsPortal, PortalUnavailable, ShortcutSpec

SMART = "smart"
LITERAL = "literal"
ASSISTANT = "assistant"

_SHORTCUTS = [
    ShortcutSpec(SMART, "Vaani: smart dictation"),
    ShortcutSpec(LITERAL, "Vaani: literal dictation"),
    ShortcutSpec(ASSISTANT, "Vaani: assistant mode"),
]


def _default_connection_factory() -> DBusRouter:
    conn = open_dbus_connection(bus="SESSION")
    return DBusRouter(conn)


class PortalHotkeyManager:
    """Matches the HotkeyService protocol (register/unregister) plus the
    same on_trigger/on_release callback contract the X11 backend uses, so
    ``run_wayland`` can wire it in identically to ``run_linux``'s X11 path.

    Note: the portal has no equivalent of X11's Esc-cancel — GlobalShortcuts
    only delivers what was bound, and a 4th "cancel" chord isn't requested
    here. Known gap, matching the existing X11 XInputHotkeyManager's Esc
    handling already being a "best effort" feature (see README's shortcut
    compatibility warning).
    """

    def __init__(self, on_trigger: Callable[[str], None], *,
                 on_release: Callable[[str], None] | None = None,
                 on_cancel: Callable[[], None] | None = None,
                 connection_factory: Callable[[], DBusRouter] | None = None,
                 logger: logging.Logger | None = None):
        self.on_trigger, self.on_release, self.on_cancel = on_trigger, on_release, on_cancel
        self.connection_factory = connection_factory or _default_connection_factory
        self.logger = logger or logging.getLogger("vaani")
        self._router: Any = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._active: set[str] = set()

    def register(self) -> None:
        router = self.connection_factory()
        try:
            portal = GlobalShortcutsPortal(router)
            portal.create_session()
            portal.bind_shortcuts(_SHORTCUTS)
            signal_queue = portal.subscribe()
        except PortalUnavailable:
            self._close_router(router)
            raise
        self._router = router
        self._stop.clear()
        self._thread = threading.Thread(target=self._listen, args=(signal_queue,), daemon=True)
        self._thread.start()

    def unregister(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._thread = None
        self._active.clear()
        if self._router is not None:
            self._close_router(self._router)
            self._router = None

    @staticmethod
    def _close_router(router: Any) -> None:
        try:
            router.close()
        except Exception:
            pass
        try:
            router.conn.close()
        except Exception:
            pass

    def _listen(self, signal_queue: "queue.Queue") -> None:
        while not self._stop.is_set():
            try:
                msg = signal_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                member = msg.header.fields.get(HeaderFields.member)
                _session_handle, shortcut_id, _timestamp, _options = msg.body
            except Exception:
                self.logger.warning("event=portal_signal_unparsable")
                continue
            if member == "Activated" and shortcut_id not in self._active:
                self._active.add(shortcut_id)
                self.on_trigger(shortcut_id)
            elif member == "Deactivated" and shortcut_id in self._active:
                self._active.discard(shortcut_id)
                if self.on_release:
                    self.on_release(shortcut_id)
