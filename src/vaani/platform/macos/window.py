"""macOS WindowControl — focus / tile / hide-others via AX + System Events (T5.1)."""
from __future__ import annotations

import subprocess
from collections.abc import Callable, Mapping
from typing import Any

from vaani.intent.schema import Result, Status, Support

Runner = Callable[..., Any]
TrustedFn = Callable[[], bool | None]

_AX_DENIED = "Accessibility permission required for window control"
_TILE_SIDES = frozenset({"left", "right", "top", "bottom"})

_SUPPORT: dict[str, tuple[Support, str]] = {
    "focus": (Support.SUPPORTED, "osascript activate (requires Accessibility)"),
    "tile": (Support.SUPPORTED, "System Events window bounds (requires Accessibility)"),
    "hide_others": (Support.SUPPORTED, "Cmd+Opt+H via System Events"),
}


def _default_runner(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, **kwargs)


def _default_trusted() -> bool | None:
    try:
        from vaani.platform.macos.trust import is_trusted

        return is_trusted()
    except Exception:
        return None


class MacWindowControl:
    """macOS leaf for ``PlatformBundle.window``. Injectable seams for tests."""

    def __init__(
        self,
        *,
        runner: Runner | None = None,
        trusted: TrustedFn | None = None,
    ) -> None:
        self._runner = runner or _default_runner
        self._trusted = trusted or _default_trusted

    def support(self) -> Mapping[str, tuple[Support, str]]:
        return dict(_SUPPORT)

    def focus(self, target: str) -> Result:
        name = (target or "").strip()
        if not name:
            return Result(
                status=Status.FAILED,
                summary="No window target",
                detail="window.focus requires a non-empty target",
                rung=4,
            )
        blocked = self._ax_block()
        if blocked is not None:
            return blocked
        script = f'tell application "{name}" to activate'
        argv = ["osascript", "-e", script]
        return self._run_ok(
            argv,
            summary=f"Focused {name}",
            fail_summary=f"Could not focus {name}",
        )

    def tile(self, side: str) -> Result:
        side_key = (side or "").strip().casefold()
        if side_key not in _TILE_SIDES:
            return Result(
                status=Status.FAILED,
                summary="Invalid tile side",
                detail=f"expected left|right|top|bottom, got {side!r}",
                rung=4,
            )
        blocked = self._ax_block()
        if blocked is not None:
            return blocked
        script = _tile_script(side_key)
        argv = ["osascript", "-e", script]
        return self._run_ok(
            argv,
            summary=f"Tiled window {side_key}",
            fail_summary=f"Could not tile window {side_key}",
        )

    def hide_others(self) -> Result:
        blocked = self._ax_block()
        if blocked is not None:
            return blocked
        # Cmd+Opt+H — hide others (UC UI-WIN-03).
        script = (
            'tell application "System Events" to keystroke "h" '
            "using {command down, option down}"
        )
        argv = ["osascript", "-e", script]
        return self._run_ok(
            argv,
            summary="Hid other windows",
            fail_summary="Could not hide other windows",
        )

    def _ax_block(self) -> Result | None:
        trusted = self._trusted()
        if trusted is False:
            return Result(
                status=Status.UNSUPPORTED,
                summary="Window control unavailable",
                detail=_AX_DENIED,
                rung=4,
            )
        return None

    def _run_ok(
        self,
        argv: list[str],
        *,
        summary: str,
        fail_summary: str,
    ) -> Result:
        evidence = tuple(argv)
        try:
            completed = self._runner(
                list(argv),
                check=False,
                capture_output=True,
                text=True,
                timeout=5.0,
            )
        except Exception as exc:
            return Result(
                status=Status.FAILED,
                summary=fail_summary,
                detail=str(exc),
                evidence=evidence,
                rung=4,
            )
        code = getattr(completed, "returncode", 1)
        if code != 0:
            detail = (
                getattr(completed, "stderr", None)
                or getattr(completed, "stdout", None)
                or f"exit {code}"
            )
            detail_text = str(detail).strip()
            # Accessibility denial often surfaces as an osascript error.
            if "not allowed assistive" in detail_text.casefold() or "1002" in detail_text:
                return Result(
                    status=Status.UNSUPPORTED,
                    summary="Window control unavailable",
                    detail=_AX_DENIED,
                    evidence=evidence,
                    rung=4,
                )
            return Result(
                status=Status.FAILED,
                summary=fail_summary,
                detail=detail_text,
                evidence=evidence,
                rung=4,
            )
        return Result(
            status=Status.OK,
            summary=summary,
            detail=summary,
            evidence=evidence,
            rung=4,
        )


def _tile_script(side: str) -> str:
    """Resize the frontmost window to a half of the primary desktop."""
    # System Events desktop bounds → {x, y, w, h}; position/size need Accessibility.
    if side == "left":
        pos, size = "{x, y}", "{halfW, h}"
    elif side == "right":
        pos, size = "{x + halfW, y}", "{halfW, h}"
    elif side == "top":
        pos, size = "{x, y}", "{w, halfH}"
    else:  # bottom
        pos, size = "{x, y + halfH}", "{w, halfH}"
    return (
        'tell application "System Events"\n'
        "  set desk to bounds of desktop 1\n"
        "  set x to item 1 of desk\n"
        "  set y to item 2 of desk\n"
        "  set w to (item 3 of desk) - x\n"
        "  set h to (item 4 of desk) - y\n"
        "  set halfW to w / 2\n"
        "  set halfH to h / 2\n"
        "  tell (first process whose frontmost is true)\n"
        "    set position of front window to "
        f"{pos}\n"
        "    set size of front window to "
        f"{size}\n"
        "  end tell\n"
        "end tell"
    )
