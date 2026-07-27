"""Honest Windows screen-capture stub for V1."""
from __future__ import annotations

from vaani.intent.schema import ScreenFrame
from vaani.vision.capture import ScreenCaptureError


class UnsupportedScreenCapture:
    def capture(self, *, display_index: int = 0) -> ScreenFrame:
        raise ScreenCaptureError("screen capture unsupported on this platform")
