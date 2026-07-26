"""T0.8: PlatformBundle optional system/window/input/terminal/screen surfaces."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from vaani.platform.linux.input import LinuxInputSynth
from vaani.platform.macos.input import MacInputSynth
from vaani.platform.protocol import (
    InputSynth,
    PlatformBundle,
    PlatformId,
    ScreenCapture,
    SystemControl,
    TerminalOpener,
    WindowControl,
)
from vaani.platform.windows.input import WindowsInputSynth


def _minimal_bundle(**overrides: object) -> PlatformBundle:
    base = dict(
        id=PlatformId.LINUX,
        settings=SimpleNamespace(),
        recorder=MagicMock(name="recorder"),
        hotkeys=MagicMock(name="hotkeys"),
        target=MagicMock(name="target"),
        delivery=MagicMock(name="delivery"),
        apps=MagicMock(name="apps"),
        browser=MagicMock(name="browser"),
        feedback=MagicMock(name="feedback"),
        key_store=MagicMock(name="key_store"),
        run=lambda _controller: 0,
    )
    base.update(overrides)
    return PlatformBundle(**base)


def test_platform_bundle_constructs_with_optional_surfaces_default_none():
    bundle = _minimal_bundle()
    assert bundle.system is None
    assert bundle.window is None
    assert bundle.input is None
    assert bundle.terminal is None
    assert bundle.screen is None


def test_platform_bundle_accepts_optional_surface_impls():
    system = MagicMock(spec=SystemControl)
    window = MagicMock(spec=WindowControl)
    synth = MagicMock(spec=InputSynth)
    terminal = MagicMock(spec=TerminalOpener)
    screen = MagicMock(spec=ScreenCapture)

    bundle = _minimal_bundle(
        system=system,
        window=window,
        input=synth,
        terminal=terminal,
        screen=screen,
    )
    assert bundle.system is system
    assert bundle.window is window
    assert bundle.input is synth
    assert bundle.terminal is terminal
    assert bundle.screen is screen


def test_build_linux_optional_surfaces_default_none(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from vaani.config import Settings
    from vaani.platform.linux.runtime import build_linux
    from vaani.platform.linux.system import LinuxSystemControl
    from vaani.platform.linux.window import LinuxWindowControl

    bundle = build_linux(Settings.from_home(tmp_path, platform="linux"))
    assert bundle.id is PlatformId.LINUX
    # T1.2/T5.1/T5.2: system + window + input wired; terminal/screen stay None.
    assert isinstance(bundle.system, LinuxSystemControl)
    assert isinstance(bundle.window, LinuxWindowControl)
    assert isinstance(bundle.input, LinuxInputSynth)
    assert bundle.terminal is None
    assert bundle.screen is None


def test_build_macos_optional_surfaces_default_none(tmp_path: Path, monkeypatch):
    from vaani.config import Settings
    from vaani.platform.macos.runtime import build_macos
    from vaani.platform.macos.system import MacSystemControl
    from vaani.platform.macos.window import MacWindowControl

    monkeypatch.setattr(
        "vaani.platform.macos.runtime.SecretServiceKeyStore",
        lambda: MagicMock(name="keystore"),
    )
    bundle = build_macos(Settings.from_home(home=tmp_path))
    assert bundle.id is PlatformId.MACOS
    # T1.2/T5.1/T5.2: system + window + input wired; terminal/screen absent.
    assert isinstance(bundle.system, MacSystemControl)
    assert isinstance(bundle.window, MacWindowControl)
    assert isinstance(bundle.input, MacInputSynth)
    assert bundle.terminal is None
    assert bundle.screen is None


def test_build_windows_optional_surfaces_default_none(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    from vaani.config import Settings
    from vaani.platform.windows.runtime import build_windows
    from vaani.platform.windows.system import WindowsSystemControl
    from vaani.platform.windows.window import WindowsWindowControl

    bundle = build_windows(Settings.from_home(tmp_path, platform="windows"))
    assert bundle.id is PlatformId.WINDOWS
    # T1.2/T5.1/T5.2: system + window + input wired; terminal/screen absent.
    assert isinstance(bundle.system, WindowsSystemControl)
    assert isinstance(bundle.window, WindowsWindowControl)
    assert isinstance(bundle.input, WindowsInputSynth)
    assert bundle.terminal is None
    assert bundle.screen is None
