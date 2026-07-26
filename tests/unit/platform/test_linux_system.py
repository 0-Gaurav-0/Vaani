"""Linux SystemControl — argv shapes and honest UNSUPPORTED via fakes (no live mutation)."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from vaani.intent.schema import Status, Support
from vaani.platform.linux.system import LinuxSystemControl
from vaani.platform.protocol import PlatformId, SystemControl


def _ok(stdout: str = "", stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=0, stdout=stdout, stderr=stderr)


def _fail(stderr: str = "boom") -> SimpleNamespace:
    return SimpleNamespace(returncode=1, stdout="", stderr=stderr)


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
) -> tuple[LinuxSystemControl, RecordingRunner]:
    rec = runner or RecordingRunner()
    which = lambda name: name if name in tools else None  # noqa: E731
    getenv = (lambda key: (env or {}).get(key)) if env is not None else (lambda _k: None)
    return (
        LinuxSystemControl(runner=rec, which=which, getenv=getenv),
        rec,
    )


def test_linux_system_control_satisfies_protocol():
    ctrl, _ = _ctrl(tools={"pactl", "loginctl"})
    assert isinstance(ctrl, SystemControl)


def test_volume_set_pactl_argv():
    ctrl, rec = _ctrl(tools={"pactl"})
    result = ctrl.volume_set(30)
    assert result.status is Status.OK
    assert result.rung == 2
    assert rec.calls == [["pactl", "set-sink-volume", "@DEFAULT_SINK@", "30%"]]
    assert result.evidence == ("pactl", "set-sink-volume", "@DEFAULT_SINK@", "30%")


def test_volume_set_clamps_and_prefers_pactl_over_wpctl():
    ctrl, rec = _ctrl(tools={"pactl", "wpctl"})
    result = ctrl.volume_set(150)
    assert result.status is Status.OK
    assert rec.calls[0] == ["pactl", "set-sink-volume", "@DEFAULT_SINK@", "100%"]


def test_volume_set_wpctl_fallback_fraction():
    ctrl, rec = _ctrl(tools={"wpctl"})
    result = ctrl.volume_set(30)
    assert result.status is Status.OK
    assert rec.calls == [["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", "0.30"]]


def test_mute_unmute_pactl_argv():
    ctrl, rec = _ctrl(tools={"pactl"})
    assert ctrl.mute(True).status is Status.OK
    assert ctrl.mute(False).status is Status.OK
    assert rec.calls == [
        ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "1"],
        ["pactl", "set-sink-mute", "@DEFAULT_SINK@", "0"],
    ]


def test_volume_unsupported_without_tools_keeps_rung():
    ctrl, rec = _ctrl(tools=set())
    result = ctrl.volume_set(30)
    assert result.status is Status.UNSUPPORTED
    assert result.rung == 2
    assert "pactl" in result.detail or "wpctl" in result.detail
    assert rec.calls == []
    support, reason = ctrl.support("volume_set")
    assert support is Support.UNSUPPORTED
    assert reason


def test_dnd_gsettings_argv_and_degraded_support():
    ctrl, rec = _ctrl(tools={"gsettings"})
    support, reason = ctrl.support("dnd")
    assert support is Support.DEGRADED
    assert "gsettings" in reason
    on = ctrl.dnd(True)
    off = ctrl.dnd(False)
    assert on.status is Status.OK
    assert off.status is Status.OK
    assert rec.calls == [
        [
            "gsettings",
            "set",
            "org.gnome.desktop.notifications",
            "show-banners",
            "false",
        ],
        [
            "gsettings",
            "set",
            "org.gnome.desktop.notifications",
            "show-banners",
            "true",
        ],
    ]


def test_lock_loginctl_argv():
    ctrl, rec = _ctrl(tools={"loginctl"})
    result = ctrl.lock()
    assert result.status is Status.OK
    assert rec.calls == [["loginctl", "lock-session"]]


def test_display_sleep_x11_xset_argv():
    ctrl, rec = _ctrl(tools={"xset"}, env={"XDG_SESSION_TYPE": "x11"})
    support, _ = ctrl.support("display_sleep")
    assert support is Support.DEGRADED
    result = ctrl.display_sleep()
    assert result.status is Status.OK
    assert rec.calls == [["xset", "dpms", "force", "off"]]


def test_display_sleep_wayland_unsupported_no_runner_call():
    ctrl, rec = _ctrl(tools={"xset"}, env={"XDG_SESSION_TYPE": "wayland"})
    support, reason = ctrl.support("display_sleep")
    assert support is Support.UNSUPPORTED
    assert "Wayland" in reason
    result = ctrl.display_sleep()
    assert result.status is Status.UNSUPPORTED
    assert result.rung == 1
    assert rec.calls == []


def test_wifi_nmcli_argv():
    ctrl, rec = _ctrl(tools={"nmcli"})
    assert ctrl.wifi(False).status is Status.OK
    assert ctrl.wifi(True).status is Status.OK
    assert rec.calls == [
        ["nmcli", "radio", "wifi", "off"],
        ["nmcli", "radio", "wifi", "on"],
    ]


def test_dns_flush_resolvectl_argv():
    ctrl, rec = _ctrl(tools={"resolvectl"})
    result = ctrl.dns_flush()
    assert result.status is Status.OK
    assert rec.calls == [["resolvectl", "flush-caches"]]


def test_trash_empty_gio_argv():
    ctrl, rec = _ctrl(tools={"gio"})
    result = ctrl.trash_empty()
    assert result.status is Status.OK
    assert rec.calls == [["gio", "trash", "--empty"]]


def test_local_ip_hostname():
    ctrl, rec = _ctrl(
        tools={"hostname"},
        runner=RecordingRunner(_ok("192.168.1.10 10.0.0.2\n")),
    )
    assert ctrl.local_ip() == "192.168.1.10"
    assert rec.calls == [["hostname", "-I"]]


def test_local_ip_ip_fallback():
    stdout = "2: wlan0    inet 10.0.0.5/24 brd 10.0.0.255 scope global wlan0\n"
    ctrl, rec = _ctrl(
        tools={"ip"},
        runner=RecordingRunner(_ok(stdout)),
    )
    assert ctrl.local_ip() == "10.0.0.5"
    assert rec.calls[0][:3] == ["ip", "-4", "-o"]


def test_local_ip_empty_when_tools_missing():
    ctrl, rec = _ctrl(tools=set())
    assert ctrl.local_ip() == ""
    assert rec.calls == []


def test_failed_command_returns_failed_with_evidence():
    ctrl, rec = _ctrl(
        tools={"loginctl"},
        runner=RecordingRunner(_fail("permission denied")),
    )
    result = ctrl.lock()
    assert result.status is Status.FAILED
    assert result.rung == 1
    assert "permission denied" in result.detail
    assert result.evidence == ("loginctl", "lock-session")


def test_build_linux_wires_system(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from vaani.config import Settings
    from vaani.platform.linux.runtime import build_linux

    bundle = build_linux(Settings.from_home(tmp_path, platform="linux"))
    assert bundle.id is PlatformId.LINUX
    assert bundle.system is not None
    assert isinstance(bundle.system, SystemControl)
    assert isinstance(bundle.system, LinuxSystemControl)


def test_list_listeners_prefers_ss():
    rec = RecordingRunner(
        _ok('LISTEN 0 128 *:3000 *:* users:(("python3",pid=4242,fd=3))\n')
    )
    ctrl, _ = _ctrl(tools={"ss", "lsof"}, runner=rec)
    holders = ctrl.list_listeners(3000)
    assert rec.calls[0][:2] == ["ss", "-lptnH"]
    assert holders[0].pid == 4242
    assert holders[0].name == "python3"


def test_list_named_pgrep_argv():
    rec = RecordingRunner(_ok("111 /usr/bin/node server.js\n"))
    ctrl, _ = _ctrl(tools={"pgrep"}, runner=rec)
    holders = ctrl.list_named("node")
    assert rec.calls == [["pgrep", "-af", "node"]]
    assert holders[0].pid == 111
    assert holders[0].name == "node"


def test_open_process_monitor_uses_available_tool():
    ctrl, rec = _ctrl(tools={"gnome-system-monitor"})
    assert ctrl.open_process_monitor().status is Status.OK
    assert rec.calls == [["gnome-system-monitor"]]
