"""Tests for the vision guide brain and capture labels."""
from __future__ import annotations

from dataclasses import dataclass

from vaani.intent.schema import ScreenFrame
from vaani.vision.capture import capture_frames
from vaani.vision.guide_brain import guide_point, make_guide_brain


@dataclass
class FakeVisionClient:
    reply: str
    calls: list[tuple[str, tuple[ScreenFrame, ...]]]

    def vision_chat(
        self, question: str, frames: tuple[ScreenFrame, ...], **_kwargs: object
    ) -> str:
        self.calls.append((question, frames))
        return self.reply


class FakeCapture:
    def capture(self, *, display_index: int = 0) -> ScreenFrame:
        return ScreenFrame(
            width=1280,
            height=720,
            data=b"jpeg",
            display_index=display_index,
        )


@dataclass
class FakeGroq:
    reply: str
    calls: list[tuple[str, tuple[ScreenFrame, ...], str, str]]

    def vision_chat(
        self,
        question: str,
        frames: tuple[ScreenFrame, ...],
        key: str,
        *,
        system: str,
    ) -> str:
        self.calls.append((question, frames, key, system))
        return self.reply


def test_guide_point_parses_fake_vision_reply_without_network():
    client = FakeVisionClient(
        "Click Export in the top right. [POINT:1200,48:Export:screen1]",
        [],
    )
    frame = ScreenFrame(width=1280, height=720, data=b"jpeg")

    speech, ops = guide_point("Where is export?", (frame,), client=client)

    assert speech == "Click Export in the top right."
    assert len(ops) == 1
    assert ops[0].kind == "point"
    assert (ops[0].x, ops[0].y, ops[0].label) == (1200, 48, "Export")
    assert client.calls == [("Where is export?", (frame,))]


def test_capture_frames_labels_images_for_the_model():
    frames = capture_frames(FakeCapture(), display_indexes=(0, 2))

    assert [frame.label for frame in frames] == [
        "screen 1 of 2 — display 1 (1280x720)",
        "screen 2 of 2 — display 3 (1280x720)",
    ]
    assert [frame.image.display_index for frame in frames] == [0, 2]


def test_make_guide_brain_binds_key_without_network():
    groq = FakeGroq("It's here. [POINT:none]", [])
    guide = make_guide_brain(groq, lambda: "test-key")
    frame = ScreenFrame(width=1280, height=720, data=b"jpeg")

    assert guide("Where is it?", (frame,)) == ("It's here.", ())
    assert groq.calls[0][:3] == ("Where is it?", (frame,), "test-key")
    assert "top-left" in groq.calls[0][3]
