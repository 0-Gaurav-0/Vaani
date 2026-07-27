"""Tests for overlay ops IPC and FakeOverlay (Task 5)."""
from __future__ import annotations

from pathlib import Path

from vaani.indicator_protocol import (
    clear_overlay,
    overlay_path,
    read_overlay,
    write_overlay,
)
from vaani.intent.schema import OverlayOp
from vaani.surface.overlay import FakeOverlay, FileOverlay


def test_overlay_path_beside_indicator_phase(tmp_path: Path) -> None:
    assert overlay_path(tmp_path) == tmp_path / "overlay_ops"


def test_write_read_clear_overlay_ops(tmp_path: Path) -> None:
    path = overlay_path(tmp_path)
    op = OverlayOp(kind="point", x=10, y=20, label="export", text="")
    write_overlay(path, [op], expires_at=123.0)

    result = read_overlay(path)
    assert result is not None
    ops, expires_at = result
    assert expires_at == 123.0
    assert len(ops) == 1
    assert ops[0].kind == "point"
    assert ops[0].x == 10
    assert ops[0].y == 20
    assert ops[0].label == "export"
    assert ops[0].text == ""

    clear_overlay(path)
    assert read_overlay(path) is None


def test_write_overlay_atomic_json_shape(tmp_path: Path) -> None:
    path = overlay_path(tmp_path)
    op = OverlayOp(kind="point", x=10, y=20, label="export", text="")
    write_overlay(path, [op], expires_at=123.0)

    raw = path.read_text(encoding="utf-8")
    assert '"expires_at": 123.0' in raw or '"expires_at": 123' in raw
    assert '"kind": "point"' in raw
    assert '"label": "export"' in raw


def test_file_overlay_writes_via_ipc(tmp_path: Path) -> None:
    overlay = FileOverlay(tmp_path)
    op = OverlayOp(kind="point", x=1, y=2, label="ok", text="")
    overlay.show([op], ttl=5.0)

    result = read_overlay(overlay_path(tmp_path))
    assert result is not None
    ops, expires_at = result
    assert len(ops) == 1
    assert ops[0].label == "ok"
    assert expires_at > 0

    overlay.clear()
    assert read_overlay(overlay_path(tmp_path)) is None


def test_fake_overlay_records_show_and_clear() -> None:
    fake = FakeOverlay()
    ops = (
        OverlayOp(kind="point", x=10, y=20, label="export", text=""),
        OverlayOp(kind="caption", x=5, y=5, text="Click here"),
    )
    fake.show(ops, ttl=8.0)

    assert len(fake.shown) == 1
    recorded_ops, ttl = fake.shown[0]
    assert recorded_ops == ops
    assert ttl == 8.0

    fake.clear()
    assert fake.clear_count == 1
