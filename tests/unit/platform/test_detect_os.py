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


def test_build_platform_rejects_macos(monkeypatch):
    monkeypatch.setattr("vaani.platform.detect_os", lambda: PlatformId.MACOS)
    with pytest.raises(UnsupportedPlatform, match="macOS"):
        build_platform()


def test_build_platform_rejects_windows(monkeypatch):
    monkeypatch.setattr("vaani.platform.detect_os", lambda: PlatformId.WINDOWS)
    with pytest.raises(UnsupportedPlatform, match="Windows"):
        build_platform()
