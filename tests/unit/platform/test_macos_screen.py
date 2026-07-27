"""T6.1: macOS in-memory ScreenCapture uses an injectable grabber."""
from __future__ import annotations

import io

import pytest

pytest.importorskip("PIL")

from PIL import Image

from vaani.config import Settings
from vaani.platform.linux.screen import UnsupportedScreenCapture as LinuxScreenCapture
from vaani.platform.macos.screen import CaptureGeometry, MacScreenCapture
from vaani.platform.windows.screen import UnsupportedScreenCapture as WindowsScreenCapture
from vaani.vision.capture import ScreenCaptureError


def _png(width: int, height: int) -> bytes:
    image = Image.new("RGB", (width, height), color=(10, 20, 30))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def test_capture_shrinks_in_memory_and_preserves_display_geometry():
    calls: list[int] = []

    def grab(display_index: int) -> tuple[bytes, CaptureGeometry]:
        calls.append(display_index)
        return _png(2000, 1000), CaptureGeometry(
            display_width=1440,
            display_height=900,
            origin_x=-1440,
            origin_y=0,
            flip_y=True,
        )

    frame = MacScreenCapture(grab_fn=grab).capture(display_index=1)

    assert calls == [1]
    assert (frame.width, frame.height) == (1280, 640)
    assert frame.data is not None and frame.data[:2] == b"\xff\xd8"
    assert frame.mime == "image/jpeg"
    assert frame.display_index == 1
    assert (frame.display_width, frame.display_height) == (1440, 900)
    assert (frame.origin_x, frame.origin_y, frame.flip_y) == (-1440, 0, True)


def test_build_macos_wires_screen_capture(tmp_path, monkeypatch):
    from unittest.mock import MagicMock

    from vaani.platform.macos.runtime import build_macos

    monkeypatch.setattr(
        "vaani.platform.macos.runtime.SecretServiceKeyStore",
        lambda: MagicMock(name="keystore"),
    )
    bundle = build_macos(Settings.from_home(home=tmp_path))

    assert isinstance(bundle.screen, MacScreenCapture)


@pytest.mark.parametrize("capture", [WindowsScreenCapture(), LinuxScreenCapture()])
def test_non_macos_captures_are_honest_stubs(capture):
    with pytest.raises(ScreenCaptureError, match="unsupported on this platform"):
        capture.capture()
