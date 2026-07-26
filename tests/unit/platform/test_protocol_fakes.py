from vaani.platform.protocol import FocusSnapshot, PlatformId


def test_focus_snapshot_equality():
    assert FocusSnapshot(token="a") == FocusSnapshot(token="a")
    assert FocusSnapshot(token="a") != FocusSnapshot(token="b")


def test_platform_id_values():
    assert PlatformId.LINUX.value == "linux"
    assert PlatformId.MACOS.value == "macos"
    assert PlatformId.WINDOWS.value == "windows"
