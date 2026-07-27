"""Tests for the macOS indicator's file-backed guide overlay loader."""
from __future__ import annotations

from pathlib import Path

from vaani.indicator_protocol import write_overlay
from vaani.intent.schema import OverlayOp
from vaani.platform.macos.indicator_app import load_overlay_draw_commands


def test_overlay_file_becomes_marker_and_caption_draw_commands(tmp_path: Path) -> None:
    path = tmp_path / "overlay_ops"
    write_overlay(
        path,
        (
            OverlayOp(kind="point", x=120, y=48, label="Export"),
            OverlayOp(kind="caption", x=120, y=76, text="Click Export"),
            OverlayOp(
                kind="tour",
                steps=(OverlayOp(kind="point", x=220, y=148, label="Next"),),
            ),
            OverlayOp(kind="unknown", x=1, y=2, text="ignored"),
        ),
        expires_at=101.0,
    )

    commands = load_overlay_draw_commands(path, now=100.0)
    assert [(command.kind, command.x, command.y, command.text) for command in commands] == [
        ("marker", 120.0, 48.0, "Export"),
        ("caption", 120.0, 76.0, "Click Export"),
        ("marker", 220.0, 148.0, "Next"),
    ]


def test_overlay_loader_auto_clears_expired_payload(tmp_path: Path) -> None:
    path = tmp_path / "overlay_ops"
    write_overlay(
        path,
        (OverlayOp(kind="point", x=120, y=48, label="Export"),),
        expires_at=100.0,
    )

    assert load_overlay_draw_commands(path, now=100.0) == ()
