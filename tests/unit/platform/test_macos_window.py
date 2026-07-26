"""T5.1: macOS WindowControl — argv shapes via fake runners; AX denial honesty."""
from __future__ import annotations

from types import SimpleNamespace

from vaani.intent.schema import Status, Support
from vaani.platform.macos.window import MacWindowControl
from vaani.platform.protocol import PlatformId, WindowControl


def _ok(stdout: str = "", stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=0, stdout=stdout, stderr=stderr)


def _fail(stderr: str = "boom") -> SimpleNamespace:
    return SimpleNamespace(returncode=1, stdout="", stderr=stderr)


class RecordingRunner:
    def __init__(self, result: SimpleNamespace | None = None) -> None:
        self.calls: list[list[str]] = []
        self.result = result or _ok()

    def __call__(self, args, **kwargs):
        self.calls.append(list(args))
        return self.result


def test_macos_window_control_satisfies_protocol():
    assert isinstance(MacWindowControl(trusted=lambda: True), WindowControl)


def test_focus_activate_argv():
    rec = RecordingRunner()
    ctrl = MacWindowControl(runner=rec, trusted=lambda: True)
    result = ctrl.focus("Chrome")
    assert result.status is Status.OK
    assert result.rung == 4
    assert rec.calls == [
        ["osascript", "-e", 'tell application "Chrome" to activate']
    ]
    assert "Chrome" in result.summary


def test_focus_ax_denied_no_runner_call():
    rec = RecordingRunner()
    ctrl = MacWindowControl(runner=rec, trusted=lambda: False)
    result = ctrl.focus("Terminal")
    assert result.status is Status.UNSUPPORTED
    assert "Accessibility" in (result.detail or "")
    assert rec.calls == []


def test_focus_osascript_assistive_error_maps_to_unsupported():
    rec = RecordingRunner(_fail("not allowed assistive access (1002)"))
    ctrl = MacWindowControl(runner=rec, trusted=lambda: True)
    result = ctrl.focus("Slack")
    assert result.status is Status.UNSUPPORTED
    assert "Accessibility" in (result.detail or "")


def test_tile_left_invokes_osascript():
    rec = RecordingRunner()
    ctrl = MacWindowControl(runner=rec, trusted=lambda: True)
    result = ctrl.tile("left")
    assert result.status is Status.OK
    assert rec.calls[0][:2] == ["osascript", "-e"]
    assert "front window" in rec.calls[0][2]


def test_tile_invalid_side():
    ctrl = MacWindowControl(runner=RecordingRunner(), trusted=lambda: True)
    result = ctrl.tile("diagonal")
    assert result.status is Status.FAILED


def test_hide_others_cmd_opt_h():
    rec = RecordingRunner()
    ctrl = MacWindowControl(runner=rec, trusted=lambda: True)
    result = ctrl.hide_others()
    assert result.status is Status.OK
    script = rec.calls[0][2]
    assert "keystroke" in script
    assert "command down" in script
    assert "option down" in script


def test_support_matrix_cells():
    ctrl = MacWindowControl(trusted=lambda: True)
    support = ctrl.support()
    assert support["focus"][0] is Support.SUPPORTED
    assert support["tile"][0] is Support.SUPPORTED
    assert support["hide_others"][0] is Support.SUPPORTED


def test_build_macos_wires_window(tmp_path, monkeypatch):
    from unittest.mock import MagicMock

    from vaani.config import Settings
    from vaani.platform.macos.runtime import build_macos
    from vaani.platform.macos.window import MacWindowControl

    monkeypatch.setattr(
        "vaani.platform.macos.runtime.SecretServiceKeyStore",
        lambda: MagicMock(name="keystore"),
    )
    bundle = build_macos(Settings.from_home(home=tmp_path))
    assert bundle.id is PlatformId.MACOS
    assert isinstance(bundle.window, MacWindowControl)
