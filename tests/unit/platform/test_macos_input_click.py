"""macOS InputSynth click unit tests."""
from __future__ import annotations

from vaani.intent.schema import Status
from vaani.platform.macos.input import MacInputSynth


class _FakeMouse:
    def __init__(self) -> None:
        self.clicks: list[tuple[float, float]] = []

    def click(self, x: float, y: float) -> None:
        self.clicks.append((x, y))


def test_mac_input_click_uses_controller() -> None:
    mouse = _FakeMouse()
    synth = MacInputSynth(controller=mouse)
    result = synth.click(12.5, 34.0)
    assert result.status is Status.OK
    assert mouse.clicks == [(12.5, 34.0)]
    assert result.evidence[0] == "click"
