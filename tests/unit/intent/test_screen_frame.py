"""Unit tests for ScreenFrame display geometry (Task 4)."""
from __future__ import annotations

from vaani.intent.schema import ScreenFrame


def test_screen_frame_defaults() -> None:
    frame = ScreenFrame(width=800, height=600)
    assert frame.data is None
    assert frame.display_index == 0
    assert frame.mime == "image/jpeg"
    assert frame.display_width == 0
    assert frame.display_height == 0
    assert frame.origin_x == 0.0
    assert frame.origin_y == 0.0
    assert frame.flip_y is False


def test_screen_frame_display_geometry() -> None:
    frame = ScreenFrame(
        width=1600,
        height=900,
        data=b"\xff\xd8",
        display_index=1,
        mime="image/png",
        display_width=2560,
        display_height=1440,
        origin_x=100.0,
        origin_y=200.0,
        flip_y=True,
    )
    assert frame.width == 1600
    assert frame.height == 900
    assert frame.data == b"\xff\xd8"
    assert frame.display_index == 1
    assert frame.mime == "image/png"
    assert frame.display_width == 2560
    assert frame.display_height == 1440
    assert frame.origin_x == 100.0
    assert frame.origin_y == 200.0
    assert frame.flip_y is True


def test_screen_frame_existing_callers_unchanged() -> None:
    frame = ScreenFrame(width=1920, height=1080, data=b"\x00")
    assert frame.width == 1920
    assert frame.height == 1080
    assert frame.data == b"\x00"
    assert frame.display_index == 0
    assert frame.mime == "image/jpeg"
    assert frame.display_width == 0
    assert frame.display_height == 0
