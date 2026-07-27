"""Parse OpenClicky-style POINT/CAPTION tags from vision model replies."""
from __future__ import annotations

import re

from vaani.intent.schema import OverlayOp

_TAG_PATTERN = re.compile(
    r"\[POINT:none\]"
    r"|\[POINT:(-?\d+),(-?\d+):([^\]]+?)(?::screen(\d+))?\]"
    r"|\[CAPTION:(-?\d+),(-?\d+):([^\]]+)\]"
)


def parse_vision_reply(text: str) -> tuple[str, tuple[OverlayOp, ...]]:
    """Return spoken text (tags stripped) and overlay ops in tag order."""
    ops: list[OverlayOp] = []

    for match in _TAG_PATTERN.finditer(text):
        if match.group(0) == "[POINT:none]":
            continue

        if match.group(1) is not None:
            x = float(match.group(1))
            y = float(match.group(2))
            label = match.group(3)
            screen = match.group(4)
            text_value = f"screen:{screen}" if screen is not None else ""
            ops.append(
                OverlayOp(kind="point", x=x, y=y, label=label, text=text_value)
            )
            continue

        x = float(match.group(5))
        y = float(match.group(6))
        caption_text = match.group(7)
        ops.append(OverlayOp(kind="caption", x=x, y=y, text=caption_text))

    speech = _TAG_PATTERN.sub("", text)
    speech = re.sub(r"\s+", " ", speech).strip()
    return speech, tuple(ops)
