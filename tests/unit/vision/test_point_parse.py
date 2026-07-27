"""Unit tests for OpenClicky-style POINT/CAPTION tag parsing."""
from __future__ import annotations

from vaani.vision.point_parse import parse_vision_reply


def test_point_with_label_and_speech():
    speech, ops = parse_vision_reply(
        "it's the blue export button near the top right [POINT:1100,40:export]"
    )
    assert "export" in speech.lower() or "blue" in speech.lower()
    assert "[POINT:" not in speech
    assert len(ops) == 1
    assert ops[0].kind == "point"
    assert ops[0].x == 1100 and ops[0].y == 40
    assert ops[0].label == "export"


def test_point_none():
    speech, ops = parse_vision_reply("nothing useful here [POINT:none]")
    assert ops == ()
    assert "POINT" not in speech


def test_caption_and_screen_suffix():
    _, ops = parse_vision_reply("here [CAPTION:10,20:save dialog] [POINT:30,40:ok:screen2]")
    assert ops[0].kind == "caption" and ops[0].text == "save dialog"
    assert ops[1].kind == "point" and ops[1].label == "ok"
    assert "2" in (ops[1].text or ops[1].label)
