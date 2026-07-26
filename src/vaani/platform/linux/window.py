"""Linux WindowControl — X11 via wmctrl/xdotool; Wayland → UNSUPPORTED (T5.1)."""
from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable, Mapping
from typing import Any

from vaani.intent.schema import Result, Status, Support

Runner = Callable[..., Any]
Whicher = Callable[[str], str | None]
Getenv = Callable[[str], str | None]

_WAYLAND_REASON = (
    "window control unsupported on Wayland "
    "(no stable activate/tile protocol; use an X11 session)"
)
_TILE_SIDES = frozenset({"left", "right", "top", "bottom"})


class LinuxWindowControl:
    """Linux leaf for ``PlatformBundle.window``. Injectable seams for tests."""

    def __init__(
        self,
        *,
        runner: Runner = subprocess.run,
        which: Whicher | None = None,
        getenv: Getenv | None = None,
    ) -> None:
        self._runner = runner
        self._which = which or shutil.which
        self._getenv = getenv or (lambda key: os.environ.get(key))

    def support(self, action: str) -> tuple[Support, str]:
        if self._is_wayland():
            return Support.UNSUPPORTED, _WAYLAND_REASON
        probes: Mapping[str, Callable[[], tuple[Support, str]]] = {
            "focus": self._support_focus,
            "tile": self._support_tile,
            "hide_others": self._support_hide_others,
        }
        probe = probes.get(action)
        if probe is None:
            return Support.UNSUPPORTED, f"unknown window action: {action}"
        return probe()

    def focus(self, target: str) -> Result:
        name = (target or "").strip()
        if not name:
            return Result(
                status=Status.FAILED,
                summary="No window target",
                detail="window.focus requires a non-empty target",
                rung=4,
            )
        blocked = self._wayland_block()
        if blocked is not None:
            return blocked
        if self._which("wmctrl"):
            argv = ["wmctrl", "-a", name]
            return self._run_ok(
                argv,
                summary=f"Focused {name}",
                fail_summary=f"Could not focus {name}",
            )
        if self._which("xdotool"):
            argv = ["xdotool", "search", "--name", name, "windowactivate"]
            return self._run_ok(
                argv,
                summary=f"Focused {name}",
                fail_summary=f"Could not focus {name}",
            )
        return Result(
            status=Status.UNSUPPORTED,
            summary="Window focus unavailable",
            detail="neither wmctrl nor xdotool found (X11)",
            rung=4,
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
        blocked = self._wayland_block()
        if blocked is not None:
            return blocked
        # Prefer GNOME/Mutter-style Super+Arrow via xdotool; wmctrl geometry next.
        if self._which("xdotool"):
            key = {
                "left": "super+Left",
                "right": "super+Right",
                "top": "super+Up",
                "bottom": "super+Down",
            }[side_key]
            argv = ["xdotool", "key", key]
            return self._run_ok(
                argv,
                summary=f"Tiled window {side_key}",
                fail_summary=f"Could not tile window {side_key}",
            )
        if self._which("wmctrl"):
            # gravity,X,Y,W,H — relative half-screen estimate (Xinerama-agnostic).
            geom = {
                "left": "0,0,0,960,1080",
                "right": "0,960,0,960,1080",
                "top": "0,0,0,1920,540",
                "bottom": "0,0,540,1920,540",
            }[side_key]
            argv = ["wmctrl", "-r", ":ACTIVE:", "-e", geom]
            return self._run_ok(
                argv,
                summary=f"Tiled window {side_key}",
                fail_summary=f"Could not tile window {side_key}",
            )
        return Result(
            status=Status.UNSUPPORTED,
            summary="Window tiling unavailable",
            detail="neither xdotool nor wmctrl found (X11)",
            rung=4,
        )

    def hide_others(self) -> Result:
        blocked = self._wayland_block()
        if blocked is not None:
            return blocked
        if self._which("wmctrl"):
            # Toggle show-desktop (_NET_SHOWING_DESKTOP).
            argv = ["wmctrl", "-k", "on"]
            return self._run_ok(
                argv,
                summary="Showed desktop",
                fail_summary="Could not show desktop",
            )
        if self._which("xdotool"):
            argv = ["xdotool", "key", "super+d"]
            return self._run_ok(
                argv,
                summary="Showed desktop",
                fail_summary="Could not show desktop",
            )
        return Result(
            status=Status.UNSUPPORTED,
            summary="Hide others unavailable",
            detail="neither wmctrl nor xdotool found (X11)",
            rung=4,
        )

    def _is_wayland(self) -> bool:
        return (self._getenv("XDG_SESSION_TYPE") or "").casefold() == "wayland"

    def _wayland_block(self) -> Result | None:
        if not self._is_wayland():
            return None
        return Result(
            status=Status.UNSUPPORTED,
            summary="Window control unavailable on Wayland",
            detail=_WAYLAND_REASON,
            rung=4,
        )

    def _support_focus(self) -> tuple[Support, str]:
        if self._which("wmctrl"):
            return Support.SUPPORTED, "wmctrl -a"
        if self._which("xdotool"):
            return Support.SUPPORTED, "xdotool search --name … windowactivate"
        return Support.UNSUPPORTED, "neither wmctrl nor xdotool found"

    def _support_tile(self) -> tuple[Support, str]:
        if self._which("xdotool"):
            return Support.DEGRADED, "xdotool key Super+Arrow (WM-dependent)"
        if self._which("wmctrl"):
            return Support.DEGRADED, "wmctrl -r :ACTIVE: -e (fixed geometry)"
        return Support.UNSUPPORTED, "neither xdotool nor wmctrl found"

    def _support_hide_others(self) -> tuple[Support, str]:
        if self._which("wmctrl"):
            return Support.SUPPORTED, "wmctrl -k on"
        if self._which("xdotool"):
            return Support.SUPPORTED, "xdotool key super+d"
        return Support.UNSUPPORTED, "neither wmctrl nor xdotool found"

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
                shell=False,
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
            return Result(
                status=Status.FAILED,
                summary=fail_summary,
                detail=str(detail).strip(),
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
