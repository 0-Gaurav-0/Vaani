"""Screenshot ↔ display ↔ overlay coordinate transforms (pure math)."""
from __future__ import annotations

from dataclasses import dataclass

_OVERLAY_PADDING = 8.0


@dataclass(frozen=True)
class DisplayGeom:
    shot_w: int
    shot_h: int
    display_w: int
    display_h: int
    origin_x: float = 0.0
    origin_y: float = 0.0
    flip_y: bool = False


def screenshot_to_global(
    x: float, y: float, geom: DisplayGeom
) -> tuple[float, float] | None:
    """Map screenshot pixel coords to global multi-monitor space."""
    if x < 0 or y < 0 or x >= geom.shot_w or y >= geom.shot_h:
        return None

    gx = x * geom.display_w / geom.shot_w
    gy = y * geom.display_h / geom.shot_h

    if geom.flip_y:
        gy = geom.display_h - gy

    return gx + geom.origin_x, gy + geom.origin_y


def global_to_overlay_local(
    gx: float,
    gy: float,
    geom: DisplayGeom,
    *,
    nudge: tuple[float, float] = (12, 12),
) -> tuple[float, float] | None:
    """Map global coords to per-display overlay window local space."""
    lx = gx - geom.origin_x
    ly = gy - geom.origin_y

    if lx < 0 or ly < 0 or lx >= geom.display_w or ly >= geom.display_h:
        return None

    lx += nudge[0]
    ly += nudge[1]

    min_coord = _OVERLAY_PADDING
    max_x = geom.display_w - _OVERLAY_PADDING
    max_y = geom.display_h - _OVERLAY_PADDING

    lx = min(max(lx, min_coord), max_x)
    ly = min(max(ly, min_coord), max_y)

    return lx, ly
