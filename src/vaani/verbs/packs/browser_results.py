"""Always-on browser search-result verbs (structured first-result open)."""
from __future__ import annotations

import logging
import re
import subprocess
from collections.abc import Callable
from typing import Any

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
from vaani.vision.coords import DisplayGeom, screenshot_to_global

PACK_NAME = "core"
BROWSER_RESULT_VERB_NAMES: frozenset[str] = frozenset({"browser.result.open"})

_ALL_SUPPORT = {
    PlatformId.LINUX: Support.SUPPORTED,
    PlatformId.MACOS: Support.SUPPORTED,
    PlatformId.WINDOWS: Support.SUPPORTED,
}

_LOG = logging.getLogger("vaani.browser_results")

ResolveResultUrl = Callable[[int], str | None]
OpenUrl = Callable[[str, Intent], str]
InputGetter = Callable[[], Any | None]
ScreenGetter = Callable[[], Any | None]
GuideBrain = Callable[[str, Any], tuple[str, tuple[OverlayOp, ...]]]
VisionClickFn = Callable[[int, Intent, Context], Result | None]


def browser_result_patterns() -> tuple[Pattern, ...]:
    """Grammar for opening the Nth SERP result (includes ASR “side” for “site”)."""
    return (
        Pattern(
            verb="browser.result.open",
            any_of=(("open",), ("first result", "first site", "first link", "first side")),
            slots=(SlotRule(name="index", value=1),),
            priority=60,
        ),
        Pattern(
            verb="browser.result.open",
            any_of=(("open the first result", "open first result"),),
            slots=(SlotRule(name="index", value=1),),
            priority=60,
            exact=False,
        ),
        Pattern(
            verb="browser.result.open",
            any_of=(("open",), ("result", "site", "link", "side")),
            slots=(
                SlotRule(
                    name="index",
                    regex=r"(?:open\s+(?:the\s+)?)?(?:(\d+)(?:st|nd|rd|th)?|first|second|third)\s+(?:result|site|link|side)",
                ),
            ),
            priority=55,
        ),
    )


def _ordinal_index(raw: Any) -> int:
    if isinstance(raw, int):
        return max(1, raw)
    text = str(raw or "1").strip().casefold()
    words = {"first": 1, "second": 2, "third": 3, "1st": 1, "2nd": 2, "3rd": 3}
    if text in words:
        return words[text]
    digits = re.search(r"\d+", text)
    if digits:
        return max(1, int(digits.group(0)))
    return 1


def _macos_resolve_result_url(index: int) -> str | None:
    """Best-effort: run JS in front Chrome/Safari to pick the Nth http(s) result.

    Fragile Google SERP heuristic — prefer injectable resolvers in tests / CDP later.
    """
    js = f"""
    (function() {{
      var n = {int(index)};
      var nodes = Array.prototype.slice.call(
        document.querySelectorAll('a[href^="http"]')
      );
      var hrefs = [];
      for (var i = 0; i < nodes.length; i++) {{
        var href = nodes[i].href || '';
        if (!href) continue;
        if (href.indexOf('google.') !== -1) continue;
        if (href.indexOf('webcache') !== -1) continue;
        hrefs.push(href);
      }}
      return hrefs[n - 1] || '';
    }})();
    """
    for app, cmd in (
        (
            "Google Chrome",
            [
                "osascript",
                "-e",
                f'tell application "Google Chrome" to tell active tab of front window to execute javascript {js!r}',
            ],
        ),
        (
            "Safari",
            [
                "osascript",
                "-e",
                f'tell application "Safari" to do JavaScript {js!r} in front document',
            ],
        ),
    ):
        try:
            completed = subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            _LOG.info("event=browser_result_resolve_fail app=%s err=%s", app, exc)
            continue
        if completed.returncode != 0:
            continue
        url = (completed.stdout or "").strip()
        if url.startswith("http://") or url.startswith("https://"):
            return url
    return None


def _default_open_url(url: str, _intent: Intent) -> str:
    import webbrowser

    webbrowser.open(url)
    return f"Opened {url}"


def _geom_from_frame(frame: Any) -> DisplayGeom:
    return DisplayGeom(
        shot_w=int(frame.width),
        shot_h=int(frame.height),
        display_w=int(frame.display_width or frame.width),
        display_h=int(frame.display_height or frame.height),
        origin_x=float(getattr(frame, "origin_x", 0.0) or 0.0),
        origin_y=float(getattr(frame, "origin_y", 0.0) or 0.0),
        flip_y=bool(getattr(frame, "flip_y", False)),
    )


def _vision_click_fallback(
    index: int,
    intent: Intent,
    _context: Context,
    *,
    get_screen: ScreenGetter | None,
    get_input: InputGetter | None,
    guide_brain: GuideBrain | None,
) -> Result | None:
    """When structured URL resolve misses, POINT the Nth result and click.

    Caller must already have passed ConfirmEngine (R2) — never auto-click unapproved.
    """
    _ = intent
    if get_screen is None or get_input is None or guide_brain is None:
        return None
    screen = get_screen()
    synth = get_input()
    if screen is None or synth is None or not hasattr(synth, "click"):
        return None
    try:
        from vaani.vision.capture import capture_frames

        frames = capture_frames(screen)
    except Exception as exc:  # noqa: BLE001
        _LOG.info("event=browser_result_vision_capture_fail err=%s", exc)
        return None
    if not frames:
        return None
    question = f"point at search result number {index} organic link"
    try:
        _speech, ops = guide_brain(question, frames)
    except Exception as exc:  # noqa: BLE001
        _LOG.info("event=browser_result_vision_brain_fail err=%s", exc)
        return None
    point_ops = [op for op in ops if op.kind == "point"]
    if not point_ops:
        return None
    op = point_ops[0]
    frame = frames[0]
    global_pt = screenshot_to_global(op.x, op.y, _geom_from_frame(frame))
    if global_pt is None:
        return None
    gx, gy = global_pt
    clicked = synth.click(gx, gy)
    if clicked.status is not Status.OK:
        return clicked
    return Result(
        status=Status.OK,
        summary=f"Clicked result {index}",
        detail=f"vision click at ({gx:.0f},{gy:.0f})",
        evidence=("vision_click", f"index={index}", f"{gx},{gy}", op.label),
        rung=2,
        overlay=(op,),
    )


def build_browser_result_verbs(
    *,
    resolve_result_url: ResolveResultUrl | None = None,
    open_url: OpenUrl | None = None,
    get_screen: ScreenGetter | None = None,
    get_input: InputGetter | None = None,
    guide_brain: GuideBrain | None = None,
    vision_click: VisionClickFn | None = None,
) -> tuple[Verb, ...]:
    resolver = resolve_result_url or _macos_resolve_result_url
    opener = open_url or _default_open_url

    def handle_open(intent: Intent, context: Context) -> Result:
        index = _ordinal_index(intent.slots.get("index", 1))
        url = resolver(index)
        if url:
            answer = opener(url, intent)
            return Result(
                status=Status.OK,
                summary=answer if isinstance(answer, str) else f"Opened {url}",
                detail=str(answer),
                evidence=(url,),
                rung=2,
            )
        if vision_click is not None:
            result = vision_click(index, intent, context)
        else:
            result = _vision_click_fallback(
                index,
                intent,
                context,
                get_screen=get_screen,
                get_input=get_input,
                guide_brain=guide_brain,
            )
        if result is not None:
            return result
        return Result(
            status=Status.PARTIAL,
            summary="Couldn't read results",
            detail=("couldn't read results; try guide or enable vision click"),
            evidence=("browser.result.open", f"index={index}", "miss"),
            rung=2,
        )

    return (
        Verb(
            name="browser.result.open",
            title="Open Nth search result",
            slots={
                "index": SlotSpec(type="int", required=False, default=1),
            },
            rung=2,
            risk=RiskClass.R2,
            requires=frozenset(),
            support=_ALL_SUPPORT,
            undo=None,
            pack=PACK_NAME,
            handler=handle_open,
        ),
    )


def register_browser_results_pack(
    registry: Registry,
    *,
    resolve_result_url: ResolveResultUrl | None = None,
    open_url: OpenUrl | None = None,
    get_screen: ScreenGetter | None = None,
    get_input: InputGetter | None = None,
    guide_brain: GuideBrain | None = None,
) -> tuple[Pattern, ...]:
    for verb in build_browser_result_verbs(
        resolve_result_url=resolve_result_url,
        open_url=open_url,
        get_screen=get_screen,
        get_input=get_input,
        guide_brain=guide_brain,
    ):
        registry.register(verb)
    return browser_result_patterns()


__all__ = [
    "BROWSER_RESULT_VERB_NAMES",
    "browser_result_patterns",
    "build_browser_result_verbs",
    "register_browser_results_pack",
]
