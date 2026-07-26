"""T5.1: Linux WindowControl — X11 argv via fakes; Wayland UNSUPPORTED."""
from __future__ import annotations

from types import SimpleNamespace

from vaani.intent.schema import Status, Support
from vaani.platform.linux.window import LinuxWindowControl
from vaani.platform.protocol import PlatformId, WindowControl


def _ok(stdout: str = "", stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=0, stdout=stdout, stderr=stderr)


class RecordingRunner:
    def __init__(self, result: SimpleNamespace | None = None) -> None:
        self.calls: list[list[str]] = []
        self.result = result or _ok()

    def __call__(self, args, **kwargs):
        assert kwargs.get("shell") is False
        self.calls.append(list(args))
        return self.result


def _ctrl(
    *,
    tools: set[str],
    runner: RecordingRunner | None = None,
    env: dict[str, str] | None = None,
) -> tuple[LinuxWindowControl, RecordingRunner]:
    rec = runner or RecordingRunner()
    which = lambda name: name if name in tools else None  # noqa: E731
    getenv = (lambda key: (env or {}).get(key)) if env is not None else (lambda _k: None)
    return LinuxWindowControl(runner=rec, which=which, getenv=getenv), rec


def test_linux_window_control_satisfies_protocol():
    ctrl, _ = _ctrl(tools={"wmctrl"}, env={"XDG_SESSION_TYPE": "x11"})
    assert isinstance(ctrl, WindowControl)


def test_focus_wmctrl_argv_on_x11():
    ctrl, rec = _ctrl(tools={"wmctrl"}, env={"XDG_SESSION_TYPE": "x11"})
    result = ctrl.focus("Chrome")
    assert result.status is Status.OK
    assert rec.calls == [["wmctrl", "-a", "Chrome"]]


def test_focus_xdotool_fallback():
    ctrl, rec = _ctrl(tools={"xdotool"}, env={"XDG_SESSION_TYPE": "x11"})
    result = ctrl.focus("Terminal")
    assert result.status is Status.OK
    assert rec.calls == [
        ["xdotool", "search", "--name", "Terminal", "windowactivate"]
    ]


def test_tile_xdotool_super_arrow():
    ctrl, rec = _ctrl(tools={"xdotool"}, env={"XDG_SESSION_TYPE": "x11"})
    result = ctrl.tile("left")
    assert result.status is Status.OK
    assert rec.calls == [["xdotool", "key", "super+Left"]]


def test_hide_others_wmctrl_show_desktop():
    ctrl, rec = _ctrl(tools={"wmctrl"}, env={"XDG_SESSION_TYPE": "x11"})
    result = ctrl.hide_others()
    assert result.status is Status.OK
    assert rec.calls == [["wmctrl", "-k", "on"]]


def test_wayland_unsupported_no_runner_call():
    ctrl, rec = _ctrl(
        tools={"wmctrl", "xdotool"},
        env={"XDG_SESSION_TYPE": "wayland"},
    )
    for result in (
        ctrl.focus("Chrome"),
        ctrl.tile("left"),
        ctrl.hide_others(),
    ):
        assert result.status is Status.UNSUPPORTED
        assert result.rung == 4
        assert "Wayland" in (result.detail or "")
    assert rec.calls == []
    support, reason = ctrl.support("focus")
    assert support is Support.UNSUPPORTED
    assert "Wayland" in reason


def test_missing_tools_unsupported_on_x11():
    ctrl, rec = _ctrl(tools=set(), env={"XDG_SESSION_TYPE": "x11"})
    result = ctrl.focus("Chrome")
    assert result.status is Status.UNSUPPORTED
    assert "wmctrl" in (result.detail or "")
    assert rec.calls == []


def test_build_linux_wires_window(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from vaani.config import Settings
    from vaani.platform.linux.runtime import build_linux

    bundle = build_linux(Settings.from_home(tmp_path, platform="linux"))
    assert bundle.id is PlatformId.LINUX
    assert isinstance(bundle.window, LinuxWindowControl)
