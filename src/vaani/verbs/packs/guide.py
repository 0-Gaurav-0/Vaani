"""Screen-guide verbs for pointing, safe offers, and replaying a pointer."""
from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import Any, Protocol

from vaani.intent.grammar import Pattern, SlotRule
from vaani.intent.schema import (
    Context,
    Intent,
    OverlayOp,
    Result,
    RiskClass,
    SlotSpec,
    Status,
    Support,
    Verb,
)
from vaani.platform.protocol import PlatformId
from vaani.verbs.registry import Registry
from vaani.vision.capture import ScreenCaptureError, capture_frames
from vaani.vision.coords import (
    DisplayGeom,
    global_to_overlay_local,
    screenshot_to_global,
)

PACK_NAME = "guide"
_OVERLAY_TAG_RE = re.compile(r"\[(?:POINT|CAPTION):[^\]]+\]")

_ALL_SUPPORT = {
    PlatformId.LINUX: Support.SUPPORTED,
    PlatformId.MACOS: Support.SUPPORTED,
    PlatformId.WINDOWS: Support.SUPPORTED,
}

ScreenGetter = Callable[[], Any | None]


class OverlaySurface(Protocol):
    """Minimal guide rendering surface kept below the surface-layer boundary."""

    def show(self, ops: Sequence[OverlayOp], *, ttl: float = 8.0) -> None: ...


OverlayGetter = Callable[[], OverlaySurface | None]
GuideBrain = Callable[[str, Sequence[Any]], tuple[str, tuple[OverlayOp, ...]]]
GuideBrainGetter = Callable[[], GuideBrain | None]
OfferHintFn = Callable[[str], str]

GUIDE_VERB_NAMES: frozenset[str] = frozenset(
    {"guide.point", "guide.offer", "guide.last_result"}
)

# Keep replay state scoped to the surface so isolated test/application sessions
# do not inherit an unrelated guide result.
_LAST_OVERLAY_OPS: dict[int, tuple[OverlayOp, ...]] = {}


def guide_patterns() -> tuple[Pattern, ...]:
    """Grammar for screen-pointing and non-mutating how-to offers."""
    return (
        Pattern(
            verb="guide.point",
            any_of=(("where's", "where is"),),
            slots=(
                SlotRule(
                    name="target",
                    regex=r"where(?:'s|\s+is)\s+(.+)",
                ),
            ),
            priority=51,
        ),
        Pattern(
            verb="guide.point",
            any_of=(("point to", "point at", ".to", ".at"),),
            slots=(
                SlotRule(name="target", regex=r"(?:point\s+|\.)\s*(?:to|at)\s+(.+)"),
            ),
            priority=51,
        ),
        Pattern(
            verb="guide.point",
            any_of=(("find", "show"), ("on screen", "on the screen")),
            slots=(
                SlotRule(
                    name="target",
                    regex=r"(?:find|show)\s+(.+?)\s+on\s+(?:the\s+)?screen",
                ),
            ),
            priority=51,
        ),
        Pattern(
            verb="guide.offer",
            any_of=(
                (
                    "how do i",
                    "how can i",
                    "how would i",
                    "how should i",
                    "how do you",
                    "how can you",
                    "how would you",
                    "how should you",
                    "how do we",
                    "how can we",
                    "how would we",
                    "how should we",
                ),
            ),
            slots=(
                SlotRule(
                    name="goal",
                    regex=r"how\s+(?:do|can|would|should)\s+(?:i|you|we)\s+(.+)",
                ),
            ),
            # This must run before the R2 port-free grammar when interrogative.
            priority=51,
        ),
    )


def _geom(frame: Any) -> DisplayGeom:
    return DisplayGeom(
        shot_w=int(frame.width),
        shot_h=int(frame.height),
        display_w=int(frame.display_width or frame.width),
        display_h=int(frame.display_height or frame.height),
        origin_x=float(frame.origin_x),
        origin_y=float(frame.origin_y),
        flip_y=bool(frame.flip_y),
    )


def _to_overlay_local(op: OverlayOp, geom: DisplayGeom) -> OverlayOp | None:
    """Convert model screenshot coords to overlay-local top-left space."""
    if op.kind == "tour":
        steps: list[OverlayOp] = []
        for step in op.steps:
            converted = _to_overlay_local(step, geom)
            if converted is not None:
                steps.append(converted)
        return replace(op, steps=tuple(steps))
    if op.kind not in {"point", "caption"}:
        return op
    global_pt = screenshot_to_global(op.x, op.y, geom)
    if global_pt is None:
        return None
    local_pt = global_to_overlay_local(global_pt[0], global_pt[1], geom)
    if local_pt is None:
        return None
    lx, ly = local_pt
    if geom.flip_y:
        ly = geom.display_h - ly
    return replace(op, x=lx, y=ly)


def _default_offer_hint(goal: str) -> str:
    """Produce a safe imperative suggestion without executing or staging it."""
    return goal.strip() or "tell me what you want to do"


def _unsupported(name: str, detail: str) -> Result:
    return Result(
        status=Status.UNSUPPORTED,
        summary=f"{name} unsupported: {detail}",
        detail=detail,
        evidence=(name, "missing"),
        rung=5,
    )


def build_guide_verbs(
    *,
    get_screen: ScreenGetter | None = None,
    get_overlay: OverlayGetter | None = None,
    guide_brain: GuideBrain | None = None,
    get_guide_brain: GuideBrainGetter | None = None,
    offer_hint_fn: OfferHintFn | None = None,
) -> tuple[Verb, ...]:
    """Build guide verbs with optional platform surfaces and vision brain."""

    def handle_point(intent: Intent, context: Context) -> Result:
        _ = context
        screen = get_screen() if get_screen is not None else None
        if screen is None:
            return _unsupported("guide.point", "screen capture is unavailable")
        overlay = get_overlay() if get_overlay is not None else None
        if overlay is None:
            return _unsupported("guide.point", "guide overlay is unavailable")
        brain = get_guide_brain() if get_guide_brain is not None else guide_brain
        if brain is None:
            return _unsupported("guide.point", "guide vision brain is unavailable")
        try:
            frames = capture_frames(screen)
        except ScreenCaptureError as exc:
            return Result(
                status=Status.FAILED,
                summary="Could not capture the screen",
                detail=str(exc),
                evidence=("guide.point", "capture_failed"),
                rung=5,
            )
        except Exception:
            return Result(
                status=Status.FAILED,
                summary="Could not capture the screen",
                detail="screen capture failed",
                evidence=("guide.point", "capture_failed"),
                rung=5,
            )

        target = str(intent.slots.get("target") or intent.utterance)
        try:
            summary, raw_ops = brain(target, frames)
        except Exception:
            return Result(
                status=Status.FAILED,
                summary="I couldn't inspect the screen.",
                detail="guide vision brain failed",
                evidence=("guide.point", "brain_failed"),
                rung=5,
            )
        geom = _geom(frames[0].image)
        ops = tuple(
            converted
            for op in raw_ops
            if (converted := _to_overlay_local(op, geom)) is not None
        )
        if ops:
            _LAST_OVERLAY_OPS[id(overlay)] = ops
        return Result(
            status=Status.OK,
            summary=_OVERLAY_TAG_RE.sub("", summary).strip(),
            detail=_OVERLAY_TAG_RE.sub("", summary).strip(),
            evidence=("guide.point",),
            rung=5,
            overlay=ops,
        )

    def handle_offer(intent: Intent, context: Context) -> Result:
        _ = context
        goal = str(intent.slots.get("goal") or intent.utterance)
        hint = (offer_hint_fn or _default_offer_hint)(goal)
        summary = f"Say: {hint}"
        return Result(
            status=Status.OK,
            summary=summary,
            detail=summary,
            evidence=("guide.offer",),
            rung=5,
        )

    def handle_last_result(intent: Intent, context: Context) -> Result:
        _ = intent, context
        overlay = get_overlay() if get_overlay is not None else None
        if overlay is None:
            return _unsupported("guide.last_result", "guide overlay is unavailable")
        ops = _LAST_OVERLAY_OPS.get(id(overlay), ())
        if not ops:
            return Result(
                status=Status.OK,
                summary="There is nothing to show from guide yet.",
                detail="no prior guide overlay",
                evidence=("guide.last_result", "empty"),
                rung=5,
            )
        return Result(
            status=Status.OK,
            summary="Showing the last guide result.",
            detail="replayed prior guide overlay",
            evidence=("guide.last_result",),
            rung=5,
            overlay=ops,
        )

    return (
        Verb(
            name="guide.point",
            title="Point to an element on screen",
            slots={"target": SlotSpec(type="str")},
            rung=5,
            risk=RiskClass.R0,
            requires=frozenset({"screen", "focus"}),
            support=_ALL_SUPPORT,
            undo=None,
            pack=PACK_NAME,
            handler=handle_point,
        ),
        Verb(
            name="guide.offer",
            title="Offer a safe command phrasing",
            slots={"goal": SlotSpec(type="str")},
            rung=5,
            risk=RiskClass.R0,
            requires=frozenset(),
            support=_ALL_SUPPORT,
            undo=None,
            pack=PACK_NAME,
            handler=handle_offer,
        ),
        Verb(
            name="guide.last_result",
            title="Show the last guide result",
            slots={},
            rung=5,
            risk=RiskClass.R0,
            requires=frozenset(),
            support=_ALL_SUPPORT,
            undo=None,
            pack=PACK_NAME,
            handler=handle_last_result,
        ),
    )


def register_guide_pack(
    registry: Registry,
    *,
    get_screen: ScreenGetter | None = None,
    get_overlay: OverlayGetter | None = None,
    guide_brain: GuideBrain | None = None,
    get_guide_brain: GuideBrainGetter | None = None,
    offer_hint_fn: OfferHintFn | None = None,
) -> tuple[Pattern, ...]:
    """Register guide verbs and return their grammar patterns."""
    for verb in build_guide_verbs(
        get_screen=get_screen,
        get_overlay=get_overlay,
        guide_brain=guide_brain,
        get_guide_brain=get_guide_brain,
        offer_hint_fn=offer_hint_fn,
    ):
        registry.register(verb)
    return guide_patterns()


__all__ = [
    "PACK_NAME",
    "GUIDE_VERB_NAMES",
    "build_guide_verbs",
    "guide_patterns",
    "register_guide_pack",
]
