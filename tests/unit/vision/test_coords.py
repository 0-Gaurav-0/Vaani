"""Unit tests for screenshot ↔ display ↔ overlay coordinate transforms."""
from __future__ import annotations

from vaani.vision.coords import (
    DisplayGeom,
    global_to_overlay_local,
    screenshot_to_global,
)


def test_identity_no_scale_no_flip():
    geom = DisplayGeom(
        shot_w=1000,
        shot_h=800,
        display_w=1000,
        display_h=800,
        origin_x=0,
        origin_y=0,
        flip_y=False,
    )
    assert screenshot_to_global(500, 400, geom) == (500, 400)


def test_scale_without_flip():
    geom = DisplayGeom(
        shot_w=1280,
        shot_h=800,
        display_w=2560,
        display_h=1600,
        origin_x=0,
        origin_y=0,
        flip_y=False,
    )
    assert screenshot_to_global(640, 400, geom) == (1280, 800)


def test_scale_and_flip():
    geom = DisplayGeom(
        shot_w=1280,
        shot_h=800,
        display_w=2560,
        display_h=1600,
        origin_x=0,
        origin_y=0,
        flip_y=True,
    )
    pt = screenshot_to_global(640, 400, geom)
    assert pt == (1280, 1600 - 800)  # scaled (1280,800) then flip Y


def test_origin_offset():
    geom = DisplayGeom(
        shot_w=1000,
        shot_h=800,
        display_w=1000,
        display_h=800,
        origin_x=100,
        origin_y=200,
        flip_y=False,
    )
    assert screenshot_to_global(50, 60, geom) == (150, 260)


def test_rejects_point_outside_shot_bounds():
    geom = DisplayGeom(
        shot_w=1280,
        shot_h=800,
        display_w=2560,
        display_h=1600,
    )
    assert screenshot_to_global(-1, 400, geom) is None
    assert screenshot_to_global(640, -1, geom) is None
    assert screenshot_to_global(1280, 400, geom) is None
    assert screenshot_to_global(640, 800, geom) is None


def test_dual_monitor_origin():
    geom = DisplayGeom(
        shot_w=1920,
        shot_h=1080,
        display_w=1920,
        display_h=1080,
        origin_x=1920,
        origin_y=0,
        flip_y=False,
    )
    assert screenshot_to_global(100, 50, geom) == (2020, 50)


def test_global_to_overlay_local_with_nudge():
    geom = DisplayGeom(
        shot_w=1920,
        shot_h=1080,
        display_w=1920,
        display_h=1080,
        origin_x=1920,
        origin_y=0,
        flip_y=False,
    )
    pt = global_to_overlay_local(2020, 50, geom)
    assert pt == (100 + 12, 50 + 12)


def test_global_to_overlay_local_outside_display():
    geom = DisplayGeom(
        shot_w=1920,
        shot_h=1080,
        display_w=1920,
        display_h=1080,
        origin_x=1920,
        origin_y=0,
        flip_y=False,
    )
    assert global_to_overlay_local(1919, 50, geom) is None
    assert global_to_overlay_local(2020, -1, geom) is None
    assert global_to_overlay_local(3840, 50, geom) is None


def test_global_to_overlay_local_clamps_to_padding():
    geom = DisplayGeom(
        shot_w=100,
        shot_h=100,
        display_w=100,
        display_h=100,
        origin_x=0,
        origin_y=0,
        flip_y=False,
    )
    pt = global_to_overlay_local(95, 95, geom, nudge=(20, 20))
    assert pt == (100 - 8, 100 - 8)
