"""T1.2: Windows SystemControl — argv shapes via fake runners; no live mutation."""
from __future__ import annotations

from pathlib import Path

import pytest

from vaani.exec.runner import Command, Completed, powershell
from vaani.intent.schema import Status, Support
from vaani.platform.protocol import PlatformId, SystemControl
from vaani.platform.windows.system import WindowsSystemControl


def _ok(argv: tuple[str, ...], stdout: str = "") -> Completed:
    return Completed(argv=argv, returncode=0, stdout=stdout, stderr="")


def _fail(argv: tuple[str, ...], stderr: str = "boom") -> Completed:
    return Completed(argv=argv, returncode=1, stdout="", stderr=stderr)


class _FakeRunner:
    def __init__(self, handler=None) -> None:
        self.calls: list[Command] = []
        self._handler = handler

    def __call__(self, cmd: Command) -> Completed:
        self.calls.append(cmd)
        if self._handler is not None:
            return self._handler(cmd)
        return _ok(cmd.argv)


def test_windows_system_control_satisfies_protocol():
    assert isinstance(WindowsSystemControl(runner=_FakeRunner()), SystemControl)


@pytest.mark.parametrize(
    ("op", "support", "reason_substr"),
    [
        ("volume_set", Support.DEGRADED, "pycaw"),
        ("mute", Support.DEGRADED, "pycaw"),
        ("dnd", Support.DEGRADED, "Focus Assist"),
        ("lock", Support.SUPPORTED, ""),
        ("display_sleep", Support.SUPPORTED, ""),
        ("wifi", Support.SUPPORTED, "elevation"),
        ("dns_flush", Support.SUPPORTED, ""),
        ("trash_empty", Support.SUPPORTED, ""),
        ("local_ip", Support.SUPPORTED, ""),
    ],
)
def test_support_cells_are_honest(op: str, support: Support, reason_substr: str):
    ctrl = WindowsSystemControl(runner=_FakeRunner())
    cell, reason = ctrl.support(op)
    assert cell is support
    if support is Support.DEGRADED:
        assert reason.strip()
        assert reason_substr.lower() in reason.lower()
    elif reason_substr:
        assert reason_substr.lower() in reason.lower()


def test_volume_and_mute_and_dnd_are_unsupported_without_escalation():
    runner = _FakeRunner()
    ctrl = WindowsSystemControl(runner=runner)

    for result in (
        ctrl.volume_set(30),
        ctrl.mute(True),
        ctrl.dnd(True),
    ):
        assert result.status is Status.UNSUPPORTED
        assert result.detail.strip()
        assert result.rung == 0
        assert result.evidence == ()
    assert runner.calls == []


def test_lock_argv_shape():
    runner = _FakeRunner()
    ctrl = WindowsSystemControl(runner=runner)
    result = ctrl.lock()
    assert result.status is Status.OK
    assert result.summary.startswith("Locked")
    assert runner.calls[0].argv == ("rundll32", "user32.dll,LockWorkStation")
    assert result.evidence == runner.calls[0].argv


def test_dns_flush_argv_shape():
    runner = _FakeRunner()
    ctrl = WindowsSystemControl(runner=runner)
    result = ctrl.dns_flush()
    assert result.status is Status.OK
    assert runner.calls[0].argv == ("ipconfig", "/flushdns")


def test_trash_empty_uses_powershell_helper():
    runner = _FakeRunner()
    ctrl = WindowsSystemControl(runner=runner)
    result = ctrl.trash_empty()
    assert result.status is Status.OK
    expected = powershell(["Clear-RecycleBin", "-Force", "-ErrorAction", "Stop"])
    assert runner.calls[0].argv == expected
    assert expected[:4] == ("powershell", "-NoProfile", "-NonInteractive", "-Command")


def test_wifi_uses_powershell_netadapter():
    runner = _FakeRunner()
    ctrl = WindowsSystemControl(runner=runner)
    off = ctrl.wifi(False)
    on = ctrl.wifi(True)
    assert off.status is Status.OK and on.status is Status.OK
    assert runner.calls[0].argv == powershell(
        ["Disable-NetAdapter", "-Name", "Wi-Fi", "-Confirm:$false"]
    )
    assert runner.calls[1].argv == powershell(
        ["Enable-NetAdapter", "-Name", "Wi-Fi", "-Confirm:$false"]
    )


def test_display_sleep_uses_powershell_helper():
    runner = _FakeRunner()
    ctrl = WindowsSystemControl(runner=runner)
    result = ctrl.display_sleep()
    assert result.status is Status.OK
    argv = runner.calls[0].argv
    assert argv[:4] == ("powershell", "-NoProfile", "-NonInteractive", "-Command")
    assert "SendMessage" in argv[4]
    assert "0xF170" in argv[4]


def test_local_ip_returns_stdout_strip():
    def handler(cmd: Command) -> Completed:
        return _ok(cmd.argv, stdout="  192.168.1.40\n")

    ctrl = WindowsSystemControl(runner=_FakeRunner(handler))
    assert ctrl.local_ip() == "192.168.1.40"


def test_local_ip_empty_on_failure():
    def handler(cmd: Command) -> Completed:
        return _fail(cmd.argv, stderr="access denied")

    ctrl = WindowsSystemControl(runner=_FakeRunner(handler))
    assert ctrl.local_ip() == ""


def test_failed_command_surfaces_stderr_and_keeps_rung_zero():
    def handler(cmd: Command) -> Completed:
        return _fail(cmd.argv, stderr="elevation required")

    ctrl = WindowsSystemControl(runner=_FakeRunner(handler))
    result = ctrl.lock()
    assert result.status is Status.FAILED
    assert "elevation required" in result.detail
    assert result.rung == 0


def test_build_windows_wires_system(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    from vaani.config import Settings
    from vaani.platform.windows.runtime import build_windows

    from vaani.platform.windows.window import WindowsWindowControl

    bundle = build_windows(Settings.from_home(tmp_path, platform="windows"))
    assert bundle.id is PlatformId.WINDOWS
    assert isinstance(bundle.system, WindowsSystemControl)
    from vaani.platform.windows.input import WindowsInputSynth

    assert isinstance(bundle.window, WindowsWindowControl)
    assert isinstance(bundle.input, WindowsInputSynth)
    # Terminal stays absent; screen is wired as an honest unsupported stub (T6).
    assert bundle.terminal is None
    from vaani.platform.windows.screen import UnsupportedScreenCapture

    assert isinstance(bundle.screen, UnsupportedScreenCapture)


def test_list_listeners_parses_powershell_rows():
    def handler(cmd: Command) -> Completed:
        return _ok(cmd.argv, stdout="PID=4242;Name=node;User=DESKTOP\\dev\n")

    ctrl = WindowsSystemControl(runner=_FakeRunner(handler))
    holders = ctrl.list_listeners(3000)
    assert len(holders) == 1
    assert holders[0].pid == 4242
    assert holders[0].name == "node"


def test_kill_pids_stop_process_script():
    fake = _FakeRunner()
    ctrl = WindowsSystemControl(runner=fake)
    result = ctrl.kill_pids([11, 12], signal="term")
    assert result.status is Status.OK
    assert "Stop-Process" in fake.calls[0].argv[-1]
    assert "11" in fake.calls[0].argv[-1]
    assert "12" in fake.calls[0].argv[-1]


def test_open_process_monitor_taskmgr():
    fake = _FakeRunner()
    ctrl = WindowsSystemControl(runner=fake)
    assert ctrl.open_process_monitor().status is Status.OK
    assert fake.calls[0].argv == ("taskmgr.exe",)
