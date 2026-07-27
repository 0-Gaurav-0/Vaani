import queue as queue_mod
import time
from types import SimpleNamespace

import pytest
from jeepney import HeaderFields

import vaani.platform.linux.wayland.hotkeys as hk
from vaani.platform.linux.wayland.hotkeys import PortalHotkeyManager, SMART, LITERAL, ASSISTANT
from vaani.platform.linux.wayland.portal import PortalUnavailable


class FakeRouter:
    def __init__(self):
        self.closed = False
        self.conn = SimpleNamespace(close=lambda: None)
    def close(self): self.closed = True


class FakePortal:
    instances = []

    def __init__(self, router, timeout=30.0):
        self.router = router
        self.bound = None
        self.queue = queue_mod.Queue()
        FakePortal.instances.append(self)

    def create_session(self): return "/session/1"
    def bind_shortcuts(self, shortcuts): self.bound = shortcuts
    def subscribe(self): return self.queue


class FailingPortal(FakePortal):
    def create_session(self): raise PortalUnavailable("no GlobalShortcuts here")


def _signal(member, session_handle, shortcut_id):
    return SimpleNamespace(
        header=SimpleNamespace(fields={HeaderFields.member: member}),
        body=(session_handle, shortcut_id, 0, {}),
    )


def _wait_until(predicate, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate(): return True
        time.sleep(0.01)
    return False


@pytest.fixture(autouse=True)
def _reset_instances():
    FakePortal.instances.clear()
    yield


def test_register_binds_all_three_chords(monkeypatch):
    monkeypatch.setattr(hk, "GlobalShortcutsPortal", FakePortal)
    router = FakeRouter()
    m = PortalHotkeyManager(lambda _: None, connection_factory=lambda: router)
    m.register()
    try:
        assert {s.id for s in FakePortal.instances[-1].bound} == {SMART, LITERAL, ASSISTANT}
    finally:
        m.unregister()


def test_activated_then_deactivated_dispatches_trigger_and_release(monkeypatch):
    monkeypatch.setattr(hk, "GlobalShortcutsPortal", FakePortal)
    triggers, releases = [], []
    router = FakeRouter()
    m = PortalHotkeyManager(triggers.append, on_release=releases.append, connection_factory=lambda: router)
    m.register()
    try:
        portal = FakePortal.instances[-1]
        portal.queue.put(_signal("Activated", "/session/1", SMART))
        assert _wait_until(lambda: triggers == [SMART])
        portal.queue.put(_signal("Deactivated", "/session/1", SMART))
        assert _wait_until(lambda: releases == [SMART])
    finally:
        m.unregister()


def test_duplicate_activated_is_ignored_until_deactivated(monkeypatch):
    monkeypatch.setattr(hk, "GlobalShortcutsPortal", FakePortal)
    triggers = []
    router = FakeRouter()
    m = PortalHotkeyManager(triggers.append, connection_factory=lambda: router)
    m.register()
    try:
        portal = FakePortal.instances[-1]
        portal.queue.put(_signal("Activated", "/session/1", LITERAL))
        assert _wait_until(lambda: triggers == [LITERAL])
        portal.queue.put(_signal("Activated", "/session/1", LITERAL))
        time.sleep(0.05)
        assert triggers == [LITERAL]
    finally:
        m.unregister()


def test_register_propagates_portal_unavailable_and_closes_router(monkeypatch):
    monkeypatch.setattr(hk, "GlobalShortcutsPortal", FailingPortal)
    router = FakeRouter()
    m = PortalHotkeyManager(lambda _: None, connection_factory=lambda: router)
    with pytest.raises(PortalUnavailable):
        m.register()
    assert router.closed


def test_unregister_stops_listener_thread(monkeypatch):
    monkeypatch.setattr(hk, "GlobalShortcutsPortal", FakePortal)
    router = FakeRouter()
    m = PortalHotkeyManager(lambda _: None, connection_factory=lambda: router)
    m.register()
    thread = m._thread
    m.unregister()
    assert router.closed
    assert not thread.is_alive()
