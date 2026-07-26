"""T5.1: Windows WindowControl — fakes only; foreground-lock honesty."""
from __future__ import annotations

from vaani.intent.schema import Status, Support
from vaani.platform.protocol import PlatformId, WindowControl
from vaani.platform.windows.window import WindowsWindowControl


def test_windows_window_control_satisfies_protocol():
    ctrl = WindowsWindowControl(
        find_window=lambda _t: 1,
        activate=lambda _h: True,
        tile_active=lambda _s: True,
        show_desktop=lambda: True,
    )
    assert isinstance(ctrl, WindowControl)


def test_focus_set_foreground_success():
    calls: list[tuple[str, object]] = []

    def find(target: str) -> int:
        calls.append(("find", target))
        return 0x42

    def activate(hwnd: int) -> bool:
        calls.append(("activate", hwnd))
        return True

    ctrl = WindowsWindowControl(find_window=find, activate=activate)
    result = ctrl.focus("Chrome")
    assert result.status is Status.OK
    assert calls == [("find", "Chrome"), ("activate", 0x42)]
    assert "SetForegroundWindow" in result.evidence
    assert "foreground-lock" in (result.detail or "")


def test_focus_foreground_lock_failure():
    ctrl = WindowsWindowControl(
        find_window=lambda _t: 99,
        activate=lambda _h: False,
    )
    result = ctrl.focus("Terminal")
    assert result.status is Status.FAILED
    assert "foreground-lock" in (result.detail or "")


def test_focus_no_matching_window():
    ctrl = WindowsWindowControl(find_window=lambda _t: None, activate=lambda _h: True)
    result = ctrl.focus("MissingApp")
    assert result.status is Status.FAILED
    assert "No window matching" in result.summary


def test_tile_and_hide_others():
    sides: list[str] = []
    desktop = {"n": 0}

    ctrl = WindowsWindowControl(
        tile_active=lambda side: sides.append(side) or True,
        show_desktop=lambda: desktop.__setitem__("n", desktop["n"] + 1) or True,
    )
    assert ctrl.tile("right").status is Status.OK
    assert sides == ["right"]
    assert ctrl.hide_others().status is Status.OK
    assert desktop["n"] == 1
    assert ctrl.tile("nope").status is Status.FAILED


def test_focus_support_is_degraded():
    ctrl = WindowsWindowControl()
    support, note = ctrl.support()["focus"]
    assert support is Support.DEGRADED
    assert "foreground-lock" in note


def test_build_windows_wires_window(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    from vaani.config import Settings
    from vaani.platform.windows.runtime import build_windows

    bundle = build_windows(Settings.from_home(tmp_path, platform="windows"))
    assert bundle.id is PlatformId.WINDOWS
    assert isinstance(bundle.window, WindowsWindowControl)
