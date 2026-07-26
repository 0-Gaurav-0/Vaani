"""Windows WindowControl — SetForegroundWindow / Snap / Win+D (T5.1).

Foreground-lock caveat: Windows may ignore ``SetForegroundWindow`` when the
calling process is not the foreground owner (UIPI / lock timeout). Callers
treat a failed activate as FAILED with that reason — never a silent no-op.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from vaani.intent.schema import Result, Status, Support

_FG_LOCK = (
    "SetForegroundWindow ignored (Windows foreground-lock / UIPI); "
    "Vaani must already own foreground or use an allowed activate path"
)
_TILE_SIDES = frozenset({"left", "right", "top", "bottom"})

_SUPPORT: dict[str, tuple[Support, str]] = {
    "focus": (
        Support.DEGRADED,
        "SetForegroundWindow; subject to foreground-lock",
    ),
    "tile": (Support.SUPPORTED, "MoveWindow half of work area (Snap-like)"),
    "hide_others": (Support.SUPPORTED, "Win+D show desktop"),
}

Win32Api = Any
FindWindowFn = Callable[[str], int | None]
ActivateFn = Callable[[int], bool]
TileFn = Callable[[str], bool]
ShowDesktopFn = Callable[[], bool]


class WindowsWindowControl:
    """Windows leaf for ``PlatformBundle.window``. Injectable Win32 seams for tests."""

    def __init__(
        self,
        *,
        find_window: FindWindowFn | None = None,
        activate: ActivateFn | None = None,
        tile_active: TileFn | None = None,
        show_desktop: ShowDesktopFn | None = None,
        win32: Win32Api | None = None,
    ) -> None:
        self._find_window = find_window
        self._activate = activate
        self._tile_active = tile_active
        self._show_desktop = show_desktop
        self._win32 = win32

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
        hwnd = self._resolve_hwnd(name)
        if not hwnd:
            return Result(
                status=Status.FAILED,
                summary=f"No window matching {name}",
                detail="EnumWindows / title match found nothing",
                evidence=("SetForegroundWindow", name),
                rung=4,
            )
        ok = self._do_activate(hwnd)
        evidence = ("SetForegroundWindow", hex(int(hwnd)), name)
        if not ok:
            return Result(
                status=Status.FAILED,
                summary=f"Could not focus {name}",
                detail=_FG_LOCK,
                evidence=evidence,
                rung=4,
            )
        return Result(
            status=Status.OK,
            summary=f"Focused {name}",
            detail=(
                f"Focused {name}. Note: SetForegroundWindow may be ignored "
                "when Vaani is not the foreground process (foreground-lock)."
            ),
            evidence=evidence,
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
        ok = self._do_tile(side_key)
        evidence = ("MoveWindow", side_key)
        if not ok:
            return Result(
                status=Status.FAILED,
                summary=f"Could not tile window {side_key}",
                detail="MoveWindow / work-area snap failed",
                evidence=evidence,
                rung=4,
            )
        return Result(
            status=Status.OK,
            summary=f"Tiled window {side_key}",
            detail=f"Tiled active window {side_key}",
            evidence=evidence,
            rung=4,
        )

    def hide_others(self) -> Result:
        ok = self._do_show_desktop()
        evidence = ("keybd_event", "Win+D")
        if not ok:
            return Result(
                status=Status.FAILED,
                summary="Could not show desktop",
                detail="Win+D synthetic keystroke failed",
                evidence=evidence,
                rung=4,
            )
        return Result(
            status=Status.OK,
            summary="Showed desktop",
            detail="Win+D (hide others / show desktop)",
            evidence=evidence,
            rung=4,
        )

    def _resolve_hwnd(self, target: str) -> int | None:
        if self._find_window is not None:
            return self._find_window(target)
        return _find_window_by_title(self._api(), target)

    def _do_activate(self, hwnd: int) -> bool:
        if self._activate is not None:
            return bool(self._activate(hwnd))
        return _set_foreground(self._api(), hwnd)

    def _do_tile(self, side: str) -> bool:
        if self._tile_active is not None:
            return bool(self._tile_active(side))
        return _tile_foreground(self._api(), side)

    def _do_show_desktop(self) -> bool:
        if self._show_desktop is not None:
            return bool(self._show_desktop())
        return _send_win_d(self._api())

    def _api(self) -> Any:
        if self._win32 is not None:
            return self._win32
        api = _load_win32()
        if api is None:
            raise RuntimeError("Win32 user32 unavailable on this host")
        return api


def _load_win32() -> Any | None:
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return None

    class _Api:
        ctypes = ctypes
        wintypes = wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)

    return _Api()


def _find_window_by_title(api: Any, target: str) -> int | None:
    needle = target.casefold()
    matches: list[int] = []

    @api.ctypes.WINFUNCTYPE(api.wintypes.BOOL, api.wintypes.HWND, api.wintypes.LPARAM)
    def _enum(hwnd, _lparam):  # type: ignore[no-untyped-def]
        if not api.user32.IsWindowVisible(hwnd):
            return True
        length = api.user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = api.ctypes.create_unicode_buffer(length + 1)
        api.user32.GetWindowTextW(hwnd, buf, length + 1)
        title = (buf.value or "").casefold()
        if needle in title:
            matches.append(int(hwnd))
        return True

    api.user32.EnumWindows(_enum, 0)
    return matches[0] if matches else None


def _set_foreground(api: Any, hwnd: int) -> bool:
    # Bring window to foreground. May still fail under foreground-lock rules.
    try:
        api.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        return bool(api.user32.SetForegroundWindow(hwnd))
    except Exception:
        return False


def _tile_foreground(api: Any, side: str) -> bool:
    try:
        hwnd = api.user32.GetForegroundWindow()
        if not hwnd:
            return False
        # SPI_GETWORKAREA = 0x0030
        rect = api.wintypes.RECT()
        if not api.user32.SystemParametersInfoW(0x0030, 0, api.ctypes.byref(rect), 0):
            return False
        left, top, right, bottom = rect.left, rect.top, rect.right, rect.bottom
        width = right - left
        height = bottom - top
        half_w = width // 2
        half_h = height // 2
        if side == "left":
            x, y, w, h = left, top, half_w, height
        elif side == "right":
            x, y, w, h = left + half_w, top, width - half_w, height
        elif side == "top":
            x, y, w, h = left, top, width, half_h
        else:
            x, y, w, h = left, top + half_h, width, height - half_h
        return bool(api.user32.MoveWindow(hwnd, x, y, w, h, True))
    except Exception:
        return False


def _send_win_d(api: Any) -> bool:
    """Synthetic Win+D (show desktop)."""
    try:
        KEYEVENTF_KEYUP = 0x0002
        VK_LWIN = 0x5B
        VK_D = 0x44
        api.user32.keybd_event(VK_LWIN, 0, 0, 0)
        api.user32.keybd_event(VK_D, 0, 0, 0)
        api.user32.keybd_event(VK_D, 0, KEYEVENTF_KEYUP, 0)
        api.user32.keybd_event(VK_LWIN, 0, KEYEVENTF_KEYUP, 0)
        return True
    except Exception:
        return False
