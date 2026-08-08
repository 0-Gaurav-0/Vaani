from types import SimpleNamespace

import pytest


class FakeOverview:
    def __init__(self, active=False):
        self.active = active
        self.set_calls = []

    def is_active(self):
        return self.active

    def set_active(self, active):
        self.set_calls.append(active)
        self.active = active


class FakeDesktop:
    def __init__(self, showing=False):
        self.showing = showing
        self.set_calls = []

    def is_showing(self):
        return self.showing

    def set_showing(self, showing):
        self.set_calls.append(showing)
        self.showing = showing


def test_four_finger_up_opens_overview_from_application_view():
    from vaani.platform.linux.desktop_gestures import DesktopGestureController

    overview = FakeOverview()
    desktop = FakeDesktop()

    DesktopGestureController(overview, desktop).handle("up")

    assert overview.set_calls == [True]
    assert desktop.set_calls == []


def test_four_finger_down_shows_desktop_from_application_view():
    from vaani.platform.linux.desktop_gestures import DesktopGestureController

    overview = FakeOverview()
    desktop = FakeDesktop()

    DesktopGestureController(overview, desktop).handle("down")

    assert overview.set_calls == []
    assert desktop.set_calls == [True]


def test_four_finger_up_closes_overview():
    from vaani.platform.linux.desktop_gestures import DesktopGestureController

    overview = FakeOverview(active=True)
    desktop = FakeDesktop()

    DesktopGestureController(overview, desktop).handle("up")

    assert overview.set_calls == [False]
    assert desktop.set_calls == []


def test_four_finger_down_closes_overview():
    from vaani.platform.linux.desktop_gestures import DesktopGestureController

    overview = FakeOverview(active=True)
    desktop = FakeDesktop()

    DesktopGestureController(overview, desktop).handle("down")

    assert overview.set_calls == [False]
    assert desktop.set_calls == []


def test_four_finger_up_restores_shown_desktop():
    from vaani.platform.linux.desktop_gestures import DesktopGestureController

    overview = FakeOverview()
    desktop = FakeDesktop(showing=True)

    DesktopGestureController(overview, desktop).handle("up")

    assert overview.set_calls == []
    assert desktop.set_calls == [False]


def test_four_finger_down_restores_shown_desktop():
    from vaani.platform.linux.desktop_gestures import DesktopGestureController

    overview = FakeOverview()
    desktop = FakeDesktop(showing=True)

    DesktopGestureController(overview, desktop).handle("down")

    assert overview.set_calls == []
    assert desktop.set_calls == [False]


def test_no_transition_occurs_when_state_query_fails():
    from vaani.platform.linux.desktop_gestures import DesktopGestureController

    class BrokenOverview(FakeOverview):
        def is_active(self):
            raise RuntimeError("D-Bus unavailable")

    overview = BrokenOverview()
    desktop = FakeDesktop()

    with pytest.raises(RuntimeError, match="D-Bus unavailable"):
        DesktopGestureController(overview, desktop).handle("down")

    assert overview.set_calls == []
    assert desktop.set_calls == []


def test_gnome_overview_adapter_reads_and_sets_dbus_property():
    from vaani.platform.linux.desktop_gestures import GnomeShellOverview

    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(stdout="(<true>,)\n")

    overview = GnomeShellOverview(runner=runner)

    assert overview.is_active() is True
    overview.set_active(False)

    assert calls[0][0][-3:] == [
        "org.freedesktop.DBus.Properties.Get",
        "org.gnome.Shell",
        "OverviewActive",
    ]
    assert calls[1][0][-4:] == [
        "org.freedesktop.DBus.Properties.Set",
        "org.gnome.Shell",
        "OverviewActive",
        "<false>",
    ]
    assert all(call[1]["check"] is True for call in calls)


def test_x11_desktop_adapter_reads_property_and_sends_ewmh_message(monkeypatch):
    from Xlib import X
    from vaani.platform.linux import desktop_gestures
    from vaani.platform.linux.desktop_gestures import X11ShowingDesktop

    class Root:
        def __init__(self):
            self.events = []

        def get_full_property(self, _atom, _property_type):
            return SimpleNamespace(value=[1])

        def send_event(self, event, **kwargs):
            self.events.append((event, kwargs))

    class Display:
        def __init__(self):
            self.root = Root()
            self.synced = 0

        def screen(self):
            return SimpleNamespace(root=self.root)

        def intern_atom(self, name):
            assert name == "_NET_SHOWING_DESKTOP"
            return 300

        def sync(self):
            self.synced += 1

    monkeypatch.setattr(
        desktop_gestures.event,
        "ClientMessage",
        lambda **kwargs: kwargs,
    )
    display = Display()
    desktop = X11ShowingDesktop(display)

    assert desktop.is_showing() is True
    desktop.set_showing(False)

    event, options = display.root.events[0]
    assert event["client_type"] == 300
    assert event["data"] == (32, [0, 0, 0, 0, 0])
    assert options["event_mask"] == (
        X.SubstructureRedirectMask | X.SubstructureNotifyMask
    )
    assert display.synced == 1


def test_cli_forwards_direction_to_controller():
    from vaani.platform.linux.desktop_gestures import main

    class Controller:
        def __init__(self):
            self.directions = []

        def handle(self, direction):
            self.directions.append(direction)

    controller = Controller()

    assert main(["down"], controller=controller) == 0
    assert controller.directions == ["down"]
