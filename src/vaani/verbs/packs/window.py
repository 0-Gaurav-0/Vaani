"""Always-on window pack: focus / tile / hide_others (UC UI-WIN-01/02/03)."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from vaani.intent.grammar import Pattern, SlotRule
from vaani.intent.schema import (
    Context,
    Intent,
    Result,
    RiskClass,
    SlotSpec,
    Status,
    Support,
    Verb,
)
from vaani.platform.protocol import PlatformId
from vaani.verbs.registry import Registry

PACK_NAME = "window"

WINDOW_VERB_NAMES: frozenset[str] = frozenset(
    {
        "window.focus",
        "window.tile",
        "window.hide_others",
    }
)

_ALL_SUPPORT = {
    PlatformId.LINUX: Support.SUPPORTED,
    PlatformId.MACOS: Support.SUPPORTED,
    PlatformId.WINDOWS: Support.SUPPORTED,
}

_FOCUS_SUPPORT = {
    PlatformId.LINUX: Support.SUPPORTED,
    PlatformId.MACOS: Support.SUPPORTED,
    # SetForegroundWindow subject to foreground-lock.
    PlatformId.WINDOWS: Support.DEGRADED,
}

_TILE_SIDES = frozenset({"left", "right", "top", "bottom"})

WindowGetter = Callable[[], Any | None]


def window_patterns() -> tuple[Pattern, ...]:
    """Grammar rows for UI-WIN-01/02/03."""
    return (
        Pattern(
            verb="window.focus",
            any_of=(("focus", "focus the", "bring up", "switch to"),),
            slots=(
                SlotRule(
                    name="target",
                    regex=(
                        r"(?:focus(?:\s+the)?|bring\s+up|switch\s+to)\s+(.+)"
                    ),
                ),
            ),
            exclude=("mode", "assist", "do not disturb", "dnd"),
            priority=42,
        ),
        Pattern(
            verb="window.tile",
            any_of=(
                (
                    "split the window left",
                    "split window left",
                    "tile left",
                    "snap left",
                    "split the window right",
                    "split window right",
                    "tile right",
                    "snap right",
                    "tile top",
                    "tile bottom",
                    "snap top",
                    "snap bottom",
                ),
            ),
            slots=(
                SlotRule(
                    name="side",
                    regex=r"\b(left|right|top|bottom)\b",
                ),
            ),
            priority=45,
        ),
        Pattern(
            verb="window.hide_others",
            any_of=(
                (
                    "hide all other windows",
                    "hide other windows",
                    "hide others",
                    "show desktop",
                    "show the desktop",
                ),
            ),
            exact=True,
            priority=45,
        ),
    )


def _missing_window(action: str) -> Result:
    return Result(
        status=Status.UNSUPPORTED,
        summary=f"{action} unavailable",
        detail="WindowControl is not available on this platform bundle",
        rung=4,
    )


def build_window_verbs(
    *,
    get_window: WindowGetter | None = None,
) -> tuple[Verb, ...]:
    """Build window verbs with an injected WindowControl seam."""

    def handle_focus(intent: Intent, _context: Context) -> Result:
        ctrl = get_window() if get_window is not None else None
        if ctrl is None:
            return _missing_window("Window focus")
        target = str(intent.slots.get("target") or intent.slots.get("name") or "").strip()
        if not target:
            return Result(
                status=Status.FAILED,
                summary="No window target",
                detail="window.focus requires a target slot",
                rung=4,
            )
        return ctrl.focus(target)

    def handle_tile(intent: Intent, _context: Context) -> Result:
        ctrl = get_window() if get_window is not None else None
        if ctrl is None:
            return _missing_window("Window tiling")
        side = str(intent.slots.get("side") or "").strip().casefold()
        if side not in _TILE_SIDES:
            # Best-effort parse from utterance when slot missing.
            raw = (intent.raw_utterance or "").casefold()
            for candidate in _TILE_SIDES:
                if candidate in raw:
                    side = candidate
                    break
        if side not in _TILE_SIDES:
            return Result(
                status=Status.FAILED,
                summary="No tile side",
                detail="window.tile requires side=left|right|top|bottom",
                rung=4,
            )
        return ctrl.tile(side)

    def handle_hide_others(_intent: Intent, _context: Context) -> Result:
        ctrl = get_window() if get_window is not None else None
        if ctrl is None:
            return _missing_window("Hide others")
        return ctrl.hide_others()

    return (
        Verb(
            name="window.focus",
            title="Focus a window or application",
            slots={"target": SlotSpec(type="str", required=True)},
            rung=4,
            risk=RiskClass.R0,
            requires=frozenset(),
            support=_FOCUS_SUPPORT,
            undo=None,
            pack=PACK_NAME,
            handler=handle_focus,
        ),
        Verb(
            name="window.tile",
            title="Tile the active window",
            slots={"side": SlotSpec(type="str", required=True)},
            rung=4,
            risk=RiskClass.R0,
            requires=frozenset(),
            support=_ALL_SUPPORT,
            undo=None,
            pack=PACK_NAME,
            handler=handle_tile,
        ),
        Verb(
            name="window.hide_others",
            title="Hide other windows / show desktop",
            slots={},
            rung=4,
            risk=RiskClass.R0,
            requires=frozenset(),
            support=_ALL_SUPPORT,
            undo=None,
            pack=PACK_NAME,
            handler=handle_hide_others,
        ),
    )


def register_window_pack(
    registry: Registry,
    *,
    get_window: WindowGetter | None = None,
) -> tuple[Pattern, ...]:
    """Register window verbs and return their grammar patterns."""
    for verb in build_window_verbs(get_window=get_window):
        registry.register(verb)
    return window_patterns()


def materialize_window_argv(
    verb_name: str,
    slots: Mapping[str, Any],
    *,
    platform: PlatformId,
) -> tuple[str, ...] | None:
    """Best-effort dry-run argv shapes for window verbs."""
    if verb_name == "window.focus":
        target = str(slots.get("target") or slots.get("name") or "App")
        if platform is PlatformId.MACOS:
            return ("osascript", "-e", f'tell application "{target}" to activate')
        if platform is PlatformId.WINDOWS:
            return ("SetForegroundWindow", target)
        return ("wmctrl", "-a", target)

    if verb_name == "window.tile":
        side = str(slots.get("side") or "left").casefold()
        if platform is PlatformId.MACOS:
            return ("osascript", "-e", f"tile front window {side}")
        if platform is PlatformId.WINDOWS:
            return ("MoveWindow", side)
        arrow = {"left": "Left", "right": "Right", "top": "Up", "bottom": "Down"}.get(
            side, "Left"
        )
        return ("xdotool", "key", f"super+{arrow}")

    if verb_name == "window.hide_others":
        if platform is PlatformId.MACOS:
            return (
                "osascript",
                "-e",
                'tell application "System Events" to keystroke "h" '
                "using {command down, option down}",
            )
        if platform is PlatformId.WINDOWS:
            return ("keybd_event", "Win+D")
        return ("wmctrl", "-k", "on")

    return None


__all__ = [
    "PACK_NAME",
    "WINDOW_VERB_NAMES",
    "build_window_verbs",
    "materialize_window_argv",
    "register_window_pack",
    "window_patterns",
]
