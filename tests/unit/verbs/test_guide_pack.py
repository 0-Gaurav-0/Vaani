"""Unit tests for the screen-guide verb pack."""
from __future__ import annotations

from dataclasses import dataclass

from vaani.apps import launch_app, resolve_app
from vaani.intent.grammar import match
from vaani.intent.router import Router
from vaani.intent.schema import Context, FocusInfo, Intent, OverlayOp, ScreenFrame, Status
from vaani.platform.protocol import PlatformId
from vaani.sites import resolve_site
from vaani.surface.overlay import FakeOverlay
from vaani.verbs.packs.guide import build_guide_verbs, guide_patterns
from vaani.verbs.packs.core import build_core_registry
from vaani.verbs.packs.registry import register_stub_packs


@dataclass
class FakeScreen:
    frame: ScreenFrame
    calls: list[int]

    def capture(self, *, display_index: int = 0) -> ScreenFrame:
        self.calls.append(display_index)
        return self.frame


def _intent(verb: str, slots: dict[str, str] | None = None) -> Intent:
    return Intent(
        verb=verb,
        slots=slots or {},
        rung=5,
        confidence=1.0,
        source="test",
        mode="guide",
        utterance=verb,
        raw_utterance=verb,
        modifiers=frozenset(),
        brain=None,
    )


def _context() -> Context:
    return Context(
        platform=PlatformId.MACOS,
        workspace=None,
        workspace_source="test",
        repo=None,
        project=None,
        focus=FocusInfo(app_id="com.example.app", window_title="Example"),
        screen=None,
        session=None,
    )


def _verbs(**kwargs):
    return {verb.name: verb for verb in build_guide_verbs(**kwargs)}


def test_point_captures_maps_and_shows_tag_free_overlay() -> None:
    screen = FakeScreen(
        ScreenFrame(
            width=100,
            height=50,
            data=b"jpeg",
            display_width=200,
            display_height=100,
            origin_x=10,
            origin_y=20,
        ),
        [],
    )
    overlay = FakeOverlay()
    brain_calls: list[tuple[str, tuple[object, ...]]] = []

    def brain(question: str, frames: tuple[object, ...]):
        brain_calls.append((question, frames))
        return "The export button is here. [POINT:50,25:Export:screen1]", (
            OverlayOp(kind="point", x=50, y=25, label="Export"),
        )

    result = _verbs(
        get_screen=lambda: screen,
        get_overlay=lambda: overlay,
        guide_brain=brain,
    )["guide.point"].handler(_intent("guide.point", {"target": "export button"}), _context())

    assert result.status is Status.OK
    assert result.summary == "The export button is here."
    assert "[POINT:" not in result.summary
    assert result.overlay[0].x == 110
    assert result.overlay[0].y == 70
    assert overlay.shown == [(result.overlay, 8.0)]
    assert screen.calls == [0]
    assert brain_calls[0][0] == "export button"


def test_point_without_screen_is_unsupported() -> None:
    result = _verbs()["guide.point"].handler(
        _intent("guide.point", {"target": "export button"}), _context()
    )

    assert result.status is Status.UNSUPPORTED
    assert "screen" in result.summary.casefold()


def test_offer_for_port_question_returns_sayable_hint() -> None:
    result = _verbs(offer_hint_fn=lambda goal: f"kill the process on {goal}")[
        "guide.offer"
    ].handler(_intent("guide.offer", {"goal": "free port 3000"}), _context())

    assert result.status is Status.OK
    assert "say:" in result.summary.casefold()
    assert "kill the process on free port 3000" in result.summary.casefold()
    assert result.pending is None


def test_last_result_without_prior_overlay_is_soft_failure() -> None:
    result = _verbs(get_overlay=lambda: FakeOverlay())["guide.last_result"].handler(
        _intent("guide.last_result"), _context()
    )

    assert result.status is Status.OK
    assert "nothing" in result.summary.casefold()
    assert result.overlay == ()


def test_guide_patterns_cover_point_and_interrogative_offer() -> None:
    patterns = guide_patterns()
    assert match("where is the export button", patterns)[0] == "guide.point"
    assert match("point at the export button", patterns)[0] == "guide.point"
    assert match("show export on the screen", patterns)[0] == "guide.point"
    assert match("how do I free port 3000", patterns)[0] == "guide.offer"


def test_enabled_guide_offer_beats_interrogative_r2_port_action() -> None:
    registry, patterns = build_core_registry(
        resolve_app_fn=resolve_app,
        launch_app_fn=launch_app,
        resolve_site_fn=resolve_site,
        open_browser_fn=lambda **_kwargs: "Opened browser.",
    )
    router = Router(
        registry,
        patterns + register_stub_packs(registry),
        resolve_app=resolve_app,
        resolve_site=resolve_site,
    )

    intent = router.route("how do I free port 3000", platform=PlatformId.LINUX)

    assert intent is not None
    assert intent.verb == "guide.offer"
    assert intent.slots["goal"] == "free port 3000"
