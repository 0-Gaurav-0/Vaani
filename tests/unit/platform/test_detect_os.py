import pytest
from vaani.platform import UnsupportedPlatform, detect_os, build_platform
from vaani.platform.protocol import PlatformId


def test_detect_os_linux():
    assert detect_os("linux") is PlatformId.LINUX
    assert detect_os("linux2") is PlatformId.LINUX


def test_detect_os_macos_windows():
    assert detect_os("darwin") is PlatformId.MACOS
    assert detect_os("win32") is PlatformId.WINDOWS


def test_detect_os_unknown():
    with pytest.raises(UnsupportedPlatform):
        detect_os("plan9")


def test_build_platform_dispatches_macos(monkeypatch):
    sentinel = object()
    monkeypatch.setattr("vaani.platform.detect_os", lambda: PlatformId.MACOS)

    def _fake_build(settings=None):
        return sentinel

    monkeypatch.setattr(
        "vaani.platform.macos.runtime.build_macos",
        _fake_build,
    )
    assert build_platform() is sentinel


def test_build_platform_dispatches_windows(monkeypatch):
    sentinel = object()
    monkeypatch.setattr("vaani.platform.detect_os", lambda: PlatformId.WINDOWS)
    monkeypatch.setattr(
        "vaani.platform.windows.runtime.build_windows",
        lambda settings=None: sentinel,
    )
    assert build_platform() is sentinel
