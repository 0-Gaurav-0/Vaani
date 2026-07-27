"""V1 in-memory macOS screen capture via Pillow's ImageGrab."""
from __future__ import annotations

import io
from collections.abc import Callable
from dataclasses import dataclass

from vaani.intent.schema import ScreenFrame
from vaani.vision.capture import ScreenCaptureError
from vaani.vision.shrink import shrink_frame_bytes


@dataclass(frozen=True)
class CaptureGeometry:
    """Display coordinates associated with an in-memory screenshot."""

    display_width: int
    display_height: int
    origin_x: float = 0.0
    origin_y: float = 0.0
    flip_y: bool = True


GrabFn = Callable[[int], tuple[bytes, CaptureGeometry]]


def _grab_primary_display(_display_index: int) -> tuple[bytes, CaptureGeometry]:
    """Capture the primary display only; ImageGrab has no display selector on macOS."""
    try:
        from PIL import ImageGrab

        image = ImageGrab.grab()
    except Exception as exc:
        raise ScreenCaptureError(f"could not capture screen: {exc}") from exc

    output = io.BytesIO()
    image.save(output, format="PNG")
    width, height = image.size
    return output.getvalue(), _primary_display_geometry(width, height)


def _primary_display_geometry(image_width: int, image_height: int) -> CaptureGeometry:
    """Read point-space display bounds, falling back to the image pixel size."""
    try:
        from AppKit import NSScreen

        frame = NSScreen.mainScreen().frame()
        width, height = round(frame.size.width), round(frame.size.height)
        origin_x, origin_y = frame.origin.x, frame.origin.y
    except Exception:
        width, height = image_width, image_height
        origin_x, origin_y = 0.0, 0.0
    # ImageGrab pixels are top-left-origin; macOS display coordinates are not.
    return CaptureGeometry(width, height, origin_x, origin_y, flip_y=True)


class MacScreenCapture:
    """Capture and shrink a macOS display without ever writing an image to disk.

    Pillow's V1 backend exposes only the primary display on macOS. A future
    ScreenCaptureKit backend can use ``display_index`` for focused displays.
    """

    def __init__(self, *, grab_fn: GrabFn | None = None) -> None:
        self._grab_fn = grab_fn or _grab_primary_display

    def capture(self, *, display_index: int = 0) -> ScreenFrame:
        try:
            data, geometry = self._grab_fn(display_index)
            data, width, height = shrink_frame_bytes(data, mime="image/png")
        except ScreenCaptureError:
            raise
        except Exception as exc:
            raise ScreenCaptureError(f"could not capture screen: {exc}") from exc

        return ScreenFrame(
            width=width,
            height=height,
            data=data,
            display_index=display_index,
            mime="image/jpeg",
            display_width=geometry.display_width,
            display_height=geometry.display_height,
            origin_x=geometry.origin_x,
            origin_y=geometry.origin_y,
            flip_y=geometry.flip_y,
        )
