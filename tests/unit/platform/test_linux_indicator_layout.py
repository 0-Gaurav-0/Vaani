import json

from vaani.platform.linux import indicator_app as ia

LAPTOP = (0, 0, 1920, 1080)
HDMI_RIGHT = (1920, 0, 2560, 1440)
HDMI_ABOVE = (0, -1440, 2560, 1440)


def test_pick_monitor_containing_nearest_and_empty():
    rects = [LAPTOP, HDMI_RIGHT]
    assert ia._pick_monitor(rects, 100, 100) == 0
    assert ia._pick_monitor(rects, 2000, 100) == 1
    # Off every monitor: nearest wins.
    assert ia._pick_monitor(rects, 5000, 200) == 1
    assert ia._pick_monitor([], 0, 0) == 0


def test_clamp_follows_the_monitor_under_the_center():
    rects = [LAPTOP, HDMI_RIGHT]
    # Center crossed onto the external display: clamp there, not to item 0.
    assert ia._clamp_to_monitors(1900, 500, 204, 42, rects) == (1920, 500)
    # Past the laptop's bottom edge: pulled back fully on-screen.
    assert ia._clamp_to_monitors(800, 1070, 204, 42, rects) == (800, 1038)


def test_zone_orientation_edges_landscape_middle_portrait():
    assert ia._zone_orientation(1011, LAPTOP) == "h"  # default bottom spot
    assert ia._zone_orientation(40, LAPTOP) == "h"
    assert ia._zone_orientation(540, LAPTOP) == "v"
    assert ia._zone_orientation(540, LAPTOP, "v") == "v"


def test_zone_orientation_hysteresis():
    edge = LAPTOP[3] * ia.EDGE_BAND
    just_inside = edge + 10  # inside the middle band, but within hysteresis
    assert ia._zone_orientation(just_inside, LAPTOP, "h") == "h"
    assert ia._zone_orientation(just_inside, LAPTOP, "v") == "v"


def test_relative_position_round_trips_and_survives_new_layout():
    x, y = ia._default_pos(*LAPTOP)
    rx, ry = ia._rel_from_pos(x, y, ia.WIDTH, ia.HEIGHT, LAPTOP)
    assert ia._pos_from_rel(rx, ry, ia.WIDTH, ia.HEIGHT, LAPTOP) == (x, y)
    # Laptop moved in root space (external plugged in above it): same spot.
    moved = (0, 1440, 1920, 1080)
    assert ia._pos_from_rel(rx, ry, ia.WIDTH, ia.HEIGHT, moved) == (x, y + 1440)


def test_answer_card_grows_up_from_bottom_and_down_from_top():
    pw, ph = ia.WIDTH, ia.HEIGHT
    # Bottom pill: card bottom stays on the pill bottom, centered on it.
    x, y = ia._answer_anchor_pos(800, 990, pw, ph, 400, 120, LAPTOP)
    assert (x, y + 120) == (800 + pw // 2 - 200, 990 + ph)
    # Top pill: card top stays on the pill top.
    _x, y = ia._answer_anchor_pos(800, 20, pw, ph, 400, 120, LAPTOP)
    assert y == 20
    # Near the right edge: clamped on-screen.
    x, _y = ia._answer_anchor_pos(1900 - pw, 990, pw, ph, 480, 120, LAPTOP)
    assert x == 1920 - 480


def test_pill_dims_swap_for_portrait():
    assert ia._pill_dims("h") == (ia.WIDTH, ia.HEIGHT)
    assert ia._pill_dims("v") == (ia.HEIGHT, ia.WIDTH)


def test_save_position_keeps_legacy_xy_and_adds_monitor(tmp_path):
    path = tmp_path / "indicator.json"
    ia._save_position(path, 10, 20, monitor="eDP-1", rx=0.5, ry=0.95)
    data = json.loads(path.read_text())
    assert data == {"x": 10, "y": 20, "monitor": "eDP-1", "rx": 0.5, "ry": 0.95}
    assert ia._load_position(path) == (10, 20)  # tk fallback still reads it
    assert ia._load_layout(path)["monitor"] == "eDP-1"
