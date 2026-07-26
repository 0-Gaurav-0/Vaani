"""macOS SystemControl argv shapes — fake runners only; no live mute/lock."""
from __future__ import annotations

from types import SimpleNamespace

from vaani.intent.schema import Status, Support
from vaani.platform.macos.system import MacSystemControl, _CGSESSION
from vaani.platform.protocol import PlatformId


def _ok(stdout: str = "", stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=0, stdout=stdout, stderr=stderr)


def _fail(stderr: str = "boom") -> SimpleNamespace:
    return SimpleNamespace(returncode=1, stdout="", stderr=stderr)


def test_support_matrix_dnd_degraded_dns_flush_r4_note():
    ctrl = MacSystemControl(runner=lambda *_a, **_k: _ok())
    matrix = ctrl.support()
    assert matrix["dnd"][0] is Support.DEGRADED
    assert "Ventura" in matrix["dnd"][1]
    assert matrix["dns_flush"][0] is Support.UNSUPPORTED
    assert "sudo" in matrix["dns_flush"][1]
    assert "R4" in matrix["dns_flush"][1]
    assert matrix["volume_set"][0] is Support.SUPPORTED
    assert matrix["lock"][0] is Support.SUPPORTED


def test_volume_set_osascript_argv():
    calls: list[list[str]] = []

    def runner(args, **_kwargs):
        calls.append(list(args))
        return _ok()

    ctrl = MacSystemControl(runner=runner)
    result = ctrl.volume_set(30)
    assert result.status is Status.OK
    assert "30%" in result.summary
    assert calls == [["osascript", "-e", "set volume output volume 30"]]


def test_volume_set_clamps_pct():
    calls: list[list[str]] = []

    def runner(args, **_kwargs):
        calls.append(list(args))
        return _ok()

    ctrl = MacSystemControl(runner=runner)
    ctrl.volume_set(150)
    ctrl.volume_set(-5)
    assert calls[0][2] == "set volume output volume 100"
    assert calls[1][2] == "set volume output volume 0"


def test_mute_unmute_osascript_argv():
    calls: list[list[str]] = []

    def runner(args, **_kwargs):
        calls.append(list(args))
        return _ok()

    ctrl = MacSystemControl(runner=runner)
    assert ctrl.mute(True).status is Status.OK
    assert ctrl.mute(False).status is Status.OK
    assert calls[0] == ["osascript", "-e", "set volume output muted true"]
    assert calls[1] == ["osascript", "-e", "set volume output muted false"]


def test_dnd_returns_unsupported_without_runner():
    calls: list[list[str]] = []

    def runner(args, **_kwargs):
        calls.append(list(args))
        return _ok()

    ctrl = MacSystemControl(runner=runner)
    result = ctrl.dnd(True)
    assert result.status is Status.UNSUPPORTED
    assert "Ventura" in result.detail
    assert calls == []


def test_lock_cgsession_argv():
    calls: list[list[str]] = []

    def runner(args, **_kwargs):
        calls.append(list(args))
        return _ok()

    ctrl = MacSystemControl(runner=runner)
    assert ctrl.lock().status is Status.OK
    assert calls == [[_CGSESSION, "-suspend"]]


def test_display_sleep_pmset_argv():
    calls: list[list[str]] = []

    def runner(args, **_kwargs):
        calls.append(list(args))
        return _ok()

    ctrl = MacSystemControl(runner=runner)
    assert ctrl.display_sleep().status is Status.OK
    assert calls == [["pmset", "displaysleepnow"]]


def test_wifi_looks_up_device_then_setairportpower():
    calls: list[list[str]] = []

    def runner(args, **_kwargs):
        calls.append(list(args))
        if args == ["networksetup", "-listallhardwareports"]:
            return _ok(
                "Hardware Port: Ethernet\nDevice: en1\n\n"
                "Hardware Port: Wi-Fi\nDevice: en0\n"
            )
        return _ok()

    ctrl = MacSystemControl(runner=runner)
    result = ctrl.wifi(False)
    assert result.status is Status.OK
    assert calls[0] == ["networksetup", "-listallhardwareports"]
    assert calls[1] == ["networksetup", "-setairportpower", "en0", "off"]
    result_on = ctrl.wifi(True)
    assert result_on.status is Status.OK
    assert calls[-1] == ["networksetup", "-setairportpower", "en0", "on"]


def test_wifi_fails_when_no_device():
    def runner(args, **_kwargs):
        if args == ["networksetup", "-listallhardwareports"]:
            return _ok("Hardware Port: Ethernet\nDevice: en1\n")
        raise AssertionError(f"unexpected argv: {args}")

    ctrl = MacSystemControl(runner=runner)
    result = ctrl.wifi(False)
    assert result.status is Status.FAILED
    assert "Wi-Fi" in result.summary


def test_dns_flush_refused_no_runner_call():
    calls: list[list[str]] = []

    def runner(args, **_kwargs):
        calls.append(list(args))
        return _ok()

    ctrl = MacSystemControl(runner=runner)
    result = ctrl.dns_flush()
    assert result.status is Status.REFUSED
    assert "sudo" in result.detail or "R4" in result.detail
    assert calls == []


def test_trash_empty_osascript_argv():
    calls: list[list[str]] = []

    def runner(args, **_kwargs):
        calls.append(list(args))
        return _ok()

    ctrl = MacSystemControl(runner=runner)
    assert ctrl.trash_empty().status is Status.OK
    assert calls == [["osascript", "-e", 'tell application "Finder" to empty trash']]


def test_local_ip_tries_en0_then_fallback():
    calls: list[list[str]] = []

    def runner(args, **_kwargs):
        calls.append(list(args))
        if args == ["ipconfig", "getifaddr", "en0"]:
            return _fail("not found")
        if args == ["ipconfig", "getifaddr", "en1"]:
            return _ok("192.168.1.40\n")
        return _fail()

    ctrl = MacSystemControl(runner=runner)
    assert ctrl.local_ip() == "192.168.1.40"
    assert calls[0] == ["ipconfig", "getifaddr", "en0"]
    assert calls[1] == ["ipconfig", "getifaddr", "en1"]


def test_failed_runner_returns_failed_result():
    ctrl = MacSystemControl(runner=lambda *_a, **_k: _fail("osascript error"))
    result = ctrl.volume_set(10)
    assert result.status is Status.FAILED
    assert "osascript error" in result.detail


def test_build_macos_wires_system(tmp_path, monkeypatch):
    from unittest.mock import MagicMock

    from vaani.config import Settings
    from vaani.platform.macos.runtime import build_macos
    from vaani.platform.macos.system import MacSystemControl

    monkeypatch.setattr(
        "vaani.platform.macos.runtime.SecretServiceKeyStore",
        lambda: MagicMock(name="keystore"),
    )
    bundle = build_macos(Settings.from_home(home=tmp_path))
    assert bundle.id is PlatformId.MACOS
    assert isinstance(bundle.system, MacSystemControl)
    assert bundle.system.support()["dns_flush"][0] is Support.UNSUPPORTED
