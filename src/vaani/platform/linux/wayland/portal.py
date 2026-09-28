"""Thin client for the XDG Desktop Portal GlobalShortcuts interface.

Verified against a live D-Bus session bus in development (connection,
send_and_get_reply, AddMatch + filter signal delivery all confirmed working
via jeepney against this machine's real bus — see jeepney's own
io/tests/test_threading.py::test_filter for the reference pattern this
mirrors). The GlobalShortcuts interface itself is written strictly to the
documented spec (https://flatpak.github.io/xdg-desktop-portal/docs/) but
could not be exercised end-to-end here: this machine's xdg-desktop-portal
build does not implement GlobalShortcuts (introspection confirms it's
absent), so it needs a real pass on a GNOME 45+/KDE Plasma 6+ Wayland
session before being trusted in production.
"""
from __future__ import annotations

import queue
from dataclasses import dataclass
from uuid import uuid4

from jeepney import DBusAddress, MatchRule, new_method_call
from jeepney.bus_messages import message_bus
from jeepney.io.threading import DBusRouter, Proxy
from jeepney.wrappers import DBusErrorResponse, unwrap_msg

PORTAL_BUS = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
SHORTCUTS_IFACE = "org.freedesktop.portal.GlobalShortcuts"
REQUEST_IFACE = "org.freedesktop.portal.Request"


class PortalUnavailable(RuntimeError):
    """Raised when the portal, or the GlobalShortcuts interface, isn't there.

    Expected on any desktop without a GlobalShortcuts-capable portal impl
    (older xdg-desktop-portal, GNOME <45, KDE Plasma <6, no portal at all);
    callers should treat this as a normal signal to fall back or exit
    cleanly, not a bug.
    """


def _unwrap(variant) -> object:
    """jeepney represents a{sv} values as (signature, value) tuples."""
    return variant[1] if isinstance(variant, tuple) and len(variant) == 2 else variant


def _shortcuts_addr() -> DBusAddress:
    return DBusAddress(PORTAL_PATH, bus_name=PORTAL_BUS, interface=SHORTCUTS_IFACE)


def _await_request(router: DBusRouter, handle: str, *, timeout: float) -> tuple[int, dict]:
    """Block for a Request's Response signal and return (code, results)."""
    rule = MatchRule(type="signal", sender=PORTAL_BUS, interface=REQUEST_IFACE,
                      member="Response", path=handle)
    try:
        Proxy(message_bus, router, timeout=timeout).AddMatch(rule)
    except DBusErrorResponse as exc:
        raise PortalUnavailable(f"could not subscribe to portal Response: {exc}") from exc
    handle_filter = router.filter(rule)
    try:
        msg = handle_filter.queue.get(timeout=timeout)
    except queue.Empty as exc:
        raise PortalUnavailable(f"no Response from portal request {handle}") from exc
    finally:
        handle_filter.close()
    response_code, results = msg.body
    return response_code, {k: _unwrap(v) for k, v in results.items()}


def _call_portal_method(router: DBusRouter, method: str, signature: str, body: tuple,
                         *, timeout: float) -> dict:
    """Call a Request-pattern portal method and wait for its real result.

    The immediate method reply only returns the request's object path; the
    actual outcome (including any needed system dialog) arrives later on a
    Request.Response signal for that path.
    """
    reply = router.send_and_get_reply(
        new_method_call(_shortcuts_addr(), method, signature, body), timeout=timeout,
    )
    try:
        handle, = unwrap_msg(reply)
    except DBusErrorResponse as exc:
        raise PortalUnavailable(f"{method} failed: {exc}") from exc
    code, results = _await_request(router, handle, timeout=timeout)
    if code != 0:
        raise PortalUnavailable(f"{method} was cancelled or failed (response={code})")
    return results


@dataclass(frozen=True)
class ShortcutSpec:
    id: str
    description: str


class GlobalShortcutsPortal:
    """Creates a GlobalShortcuts session and binds a fixed set of shortcuts."""

    def __init__(self, router: DBusRouter, *, timeout: float = 30.0):
        self.router = router
        self.timeout = timeout
        self.session_handle: str | None = None

    def create_session(self) -> str:
        options = {
            "handle_token": ("s", f"vaani_{uuid4().hex}"),
            "session_handle_token": ("s", f"vaani_session_{uuid4().hex}"),
        }
        results = _call_portal_method(
            self.router, "CreateSession", "a{sv}", (options,), timeout=self.timeout,
        )
        self.session_handle = results["session_handle"]
        return self.session_handle

    def bind_shortcuts(self, shortcuts: list[ShortcutSpec]) -> None:
        if self.session_handle is None:
            raise RuntimeError("create_session() must succeed before bind_shortcuts()")
        shortcut_args = [
            (spec.id, {"description": ("s", spec.description)}) for spec in shortcuts
        ]
        options = {"handle_token": ("s", f"vaani_{uuid4().hex}")}
        _call_portal_method(
            self.router, "BindShortcuts", "oa(sa{sv})sa{sv}",
            (self.session_handle, shortcut_args, "", options), timeout=self.timeout,
        )

    def subscribe(self) -> "queue.Queue":
        """Return a queue receiving raw Activated/Deactivated signal messages."""
        shared: queue.Queue = queue.Queue()
        proxy = Proxy(message_bus, self.router, timeout=self.timeout)
        for member in ("Activated", "Deactivated"):
            rule = MatchRule(type="signal", sender=PORTAL_BUS, interface=SHORTCUTS_IFACE,
                              member=member, path=PORTAL_PATH)
            try:
                proxy.AddMatch(rule)
            except DBusErrorResponse as exc:
                raise PortalUnavailable(f"could not subscribe to {member}: {exc}") from exc
            self.router.filter(rule, queue=shared)
        return shared
