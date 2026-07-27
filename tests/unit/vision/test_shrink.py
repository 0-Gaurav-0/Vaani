"""Unit tests for in-memory screenshot shrink."""
from __future__ import annotations

import io

import pytest

pytest.importorskip("PIL")

from PIL import Image

from vaani.vision.shrink import shrink_frame_bytes


def _make_png_bytes(width: int, height: int) -> bytes:
    img = Image.new("RGB", (width, height), color=(255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_shrink_max_edge_in_memory():
    src = _make_png_bytes(2000, 1000)
    out, w, h = shrink_frame_bytes(src, mime="image/png", max_edge=1280, quality=80)
    assert max(w, h) <= 1280
    assert out[:2] == b"\xff\xd8"  # jpeg
    assert w == 1280
    assert h == 640
