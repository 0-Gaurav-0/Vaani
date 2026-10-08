"""Linux floating recording indicator — transparent GTK4 overlay.

Glass capsule (translucent body, sheen, rim light, soft shadow) drawn with
cairo; Mutter on X11 cannot blur behind client windows, so the glass is faked.
Positions via X11 (python-xlib or ctypes libX11) as an override-redirect window
so Mutter cannot leave it stuck at (0,0). Defaults to center-bottom of the
primary monitor; position is remembered relative to its monitor so display
hotplug keeps the pill where it was. Near the top/bottom edge the pill is
landscape; elsewhere it turns portrait.
"""
from __future__ import annotations

import ctypes
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Any

WIDTH = 204
HEIGHT = 42
# Transparent margin around the content so the soft shadow has room. Input
# region covers only the content, so the margin is click-through.
SHADOW_PAD = 16
ANSWER_MIN_WIDTH = 200
ANSWER_MAX_WIDTH = 480
ANSWER_TEXT_MIN_HEIGHT = 44
MARGIN_BOTTOM = 48
BAR_COUNT = 22
HIT_PAD = 36
# Pointer travel before a press turns into a drag instead of a click.
DRAG_THRESHOLD = 4.0
# Pill center within this fraction of the monitor height from the top or
# bottom edge ⇒ landscape; anywhere else ⇒ portrait. Hysteresis stops flicker.
EDGE_BAND = 0.26
EDGE_HYSTERESIS = 0.03
# Ignore clicks right after the pill maps — Mutter/GTK sometimes delivers a
# ghost press on the left cancel pad ~1s after spawn (kills snap sessions).
CLICK_GRACE_S = 0.75
ANSWER_AUTO_DISMISS_MS = 9000
ANSWER_CLARIFY_DISMISS_MS = 45000
ANSWER_LEAVE_DISMISS_MS = 5000
ANSWER_TEXT_LEFT = 18.0
ANSWER_PAD_RIGHT = 18.0
ANSWER_Q_SIZE = 13.0
ANSWER_A_SIZE = 15.0
ANSWER_Q_LINE = 17.0
ANSWER_A_LINE = 19.0
ANSWER_GAP = 14.0
ANSWER_TOP = 20.0
ANSWER_TEXT_BOTTOM_PAD = 16.0
ANSWER_RADIUS = 18.0
ANSWER_ANIM_MS = 280
# ---- look (glass capsule) ----
FONT = "Ubuntu"
MONO_FONT = "Ubuntu Mono"
GLASS_TOP = (0.17, 0.18, 0.22, 0.74)
GLASS_BOTTOM = (0.05, 0.05, 0.07, 0.86)
DICTATION_ACCENT = (0.96, 0.96, 0.97)
ASSISTANT_ACCENT = (0.66, 0.55, 0.98)  # violet
WARN_ACCENT = (0.98, 0.75, 0.14)  # amber, last minute
DANGER_ACCENT = (0.97, 0.33, 0.33)  # red, last 15s
APPEAR_MS = 200
TIMER_AFTER_S = 20.0

# Button press feedback: quick squeeze + halo ring.
PRESS_ANIM_MS = 220
# Hidden (idle) pill parks here: mapped but invisible and click-through.
OFFSCREEN = (-4000, -4000)
# If the daemon never answers a pill click, hide locally after this.
REQUEST_FALLBACK_HIDE_MS = 1500

Rect = tuple[int, int, int, int]


def _dbg(msg: str) -> None:
    if os.environ.get("VAANI_INDICATOR_DEBUG"):
        print(f"[vaani] indicator debug: {msg}", flush=True)


def _ease_smooth(t: float) -> float:
    t = max(0.0, min(1.0, float(t)))
    return t * t * (3.0 - 2.0 * t)


def _load_position(path: Path) -> tuple[int, int] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if "y" not in data:
            return None
        return int(data["x"]), int(data["y"])
    except Exception:
        return None


def _load_layout(path: Path) -> dict[str, Any]:
    """Saved pill layout: absolute x/y plus monitor-relative center if known."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_position(
    path: Path,
    x: int,
    y: int,
    *,
    monitor: str | None = None,
    rx: float | None = None,
    ry: float | None = None,
) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        data: dict[str, Any] = {"x": int(x), "y": int(y)}
        if monitor is not None and rx is not None and ry is not None:
            data.update(monitor=monitor, rx=round(float(rx), 4), ry=round(float(ry), 4))
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        pass


def _pill_dims(orient: str) -> tuple[int, int]:
    return (HEIGHT, WIDTH) if orient == "v" else (WIDTH, HEIGHT)


def _pick_monitor(rects: list[Rect], cx: float, cy: float, primary: int = 0) -> int:
    """Index of the monitor containing (cx, cy), else the nearest, else primary."""
    if not rects:
        return 0
    best, best_d = None, None
    for i, (mx, my, mw, mh) in enumerate(rects):
        if mx <= cx < mx + mw and my <= cy < my + mh:
            return i
        dx = max(mx - cx, 0.0, cx - (mx + mw))
        dy = max(my - cy, 0.0, cy - (my + mh))
        d = dx * dx + dy * dy
        if best_d is None or d < best_d:
            best, best_d = i, d
    return best if best is not None else max(0, min(primary, len(rects) - 1))


def _clamp_pos(
    x: int, y: int, ox: int, oy: int, sw: int, sh: int, *, w: int = WIDTH, h: int = HEIGHT
) -> tuple[int, int]:
    return (
        max(ox, min(int(x), ox + max(0, sw - w))),
        max(oy, min(int(y), oy + max(0, sh - h))),
    )


def _clamp_to_monitors(
    x: int, y: int, w: int, h: int, rects: list[Rect], primary: int = 0
) -> tuple[int, int]:
    """Keep the content fully on the monitor its center is on (or nearest)."""
    if not rects:
        return int(x), int(y)
    mx, my, mw, mh = rects[_pick_monitor(rects, x + w / 2.0, y + h / 2.0, primary)]
    return _clamp_pos(x, y, mx, my, mw, mh, w=w, h=h)


def _default_pos(
    ox: int, oy: int, sw: int, sh: int, *, w: int = WIDTH, h: int = HEIGHT
) -> tuple[int, int]:
    return ox + max(0, (sw - w) // 2), oy + max(0, sh - h - MARGIN_BOTTOM)


def _zone_orientation(cy: float, rect: Rect, current: str = "h") -> str:
    """'h' near the top/bottom edge of the monitor, 'v' in the middle band."""
    _mx, my, _mw, mh = rect
    if mh <= 0:
        return "h"
    band = EDGE_BAND + (EDGE_HYSTERESIS if current == "h" else -EDGE_HYSTERESIS)
    rel = (cy - my) / float(mh)
    return "h" if rel < band or rel > 1.0 - band else "v"


def _rel_from_pos(x: int, y: int, w: int, h: int, rect: Rect) -> tuple[float, float]:
    mx, my, mw, mh = rect
    return (
        max(0.0, min(1.0, (x + w / 2.0 - mx) / max(1, mw))),
        max(0.0, min(1.0, (y + h / 2.0 - my) / max(1, mh))),
    )


def _pos_from_rel(rx: float, ry: float, w: int, h: int, rect: Rect) -> tuple[int, int]:
    mx, my, mw, mh = rect
    x = int(round(mx + rx * mw - w / 2.0))
    y = int(round(my + ry * mh - h / 2.0))
    return _clamp_pos(x, y, mx, my, mw, mh, w=w, h=h)


def _resolve_spot(
    spot: dict[str, Any],
    names: list[str],
    rects: list[Rect],
    primary: int,
    w: int,
    h: int,
) -> tuple[Rect, float, float]:
    """Monitor + relative center for a remembered spot on the current layout.

    The spot's own monitor if it is still connected; otherwise the primary
    monitor at the same relative place; with no spot, primary bottom-center.
    """
    mon = spot.get("monitor")
    has_rel = "rx" in spot and "ry" in spot
    if mon in names and has_rel:
        return rects[names.index(mon)], float(spot["rx"]), float(spot["ry"])
    rect = rects[max(0, min(primary, len(rects) - 1))]
    if has_rel:
        return rect, float(spot["rx"]), float(spot["ry"])
    dx, dy = _default_pos(*rect, w=w, h=h)
    rx, ry = _rel_from_pos(dx, dy, w, h, rect)
    return rect, rx, ry


def _spot_for_pos(
    x: int, y: int, w: int, h: int, names: list[str], rects: list[Rect], primary: int
) -> dict[str, Any]:
    i = _pick_monitor(rects, x + w / 2.0, y + h / 2.0, primary)
    rx, ry = _rel_from_pos(x, y, w, h, rects[i])
    return {"monitor": names[i], "rx": rx, "ry": ry}


def _answer_anchor_pos(
    pill_x: int,
    pill_y: int,
    pill_w: int,
    pill_h: int,
    new_w: int,
    new_h: int,
    rect: Rect,
) -> tuple[int, int]:
    """Place the answer card on the pill, growing away from the nearer edge.

    Horizontally centered on the pill; grows upward when the pill sits in the
    lower half of its monitor, downward otherwise; then clamped on-screen.
    """
    mx, my, mw, mh = rect
    nx = int(round(pill_x + pill_w / 2.0 - new_w / 2.0))
    if pill_y + pill_h / 2.0 >= my + mh / 2.0:
        ny = pill_y + pill_h - new_h
    else:
        ny = pill_y
    return _clamp_pos(nx, ny, mx, my, mw, mh, w=new_w, h=new_h)


def _wrap_cairo_text(cr, text: str, max_width: float) -> list[str]:
    # Explicit newlines are hard breaks (clarify / confirm options are one per line).
    if "\n" in (text or ""):
        out: list[str] = []
        for part in (text or "").split("\n"):
            out.extend(_wrap_cairo_text(cr, part, max_width))
        return out or [""]
    words = (text or "").split()
    if not words:
        return [""]
    lines: list[str] = []
    current = ""
    for word in words:
        trial = word if not current else f"{current} {word}"
        if cr.text_extents(trial).width <= max_width or not current:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _measure_answer_size(
    question: str,
    answer: str,
    *,
    cairo_mod: Any | None = None,
) -> tuple[int, int]:
    """Return answer-card size (text only; no control strip)."""
    cr = None
    if cairo_mod is not None:
        try:
            surface = cairo_mod.ImageSurface(cairo_mod.FORMAT_ARGB32, 8, 8)
            cr = cairo_mod.Context(surface)
            cr.select_font_face(FONT, cairo_mod.FONT_SLANT_NORMAL, cairo_mod.FONT_WEIGHT_NORMAL)
        except Exception:
            cr = None
    if cr is None:
        try:
            import cairo as cairo_mod

            surface = cairo_mod.ImageSurface(cairo_mod.FORMAT_ARGB32, 8, 8)
            cr = cairo_mod.Context(surface)
            cr.select_font_face(
                FONT, cairo_mod.FONT_SLANT_NORMAL, cairo_mod.FONT_WEIGHT_NORMAL
            )
        except Exception:
            cr = None

    def _natural(text: str, size: float) -> float:
        blob = " ".join((text or "").split()) or " "
        if cr is None:
            return len(blob) * size * 0.55
        cr.set_font_size(size)
        return float(cr.text_extents(blob).width)

    def _wrap(text: str, size: float, max_width: float) -> list[str]:
        if cr is None and "\n" in (text or ""):
            return [ln for part in text.split("\n") for ln in _wrap(part, size, max_width)]
        if cr is None:
            words = (text or "").split() or [""]
            lines: list[str] = []
            current = ""
            avg = size * 0.55
            for word in words:
                trial = word if not current else f"{current} {word}"
                if len(trial) * avg <= max_width or not current:
                    current = trial
                else:
                    lines.append(current)
                    current = word
            if current:
                lines.append(current)
            return lines
        cr.set_font_size(size)
        return _wrap_cairo_text(cr, text, max_width)

    def _line_width(text: str, size: float) -> float:
        if cr is None:
            return len(text) * size * 0.55
        cr.set_font_size(size)
        return float(cr.text_extents(text).width)

    needed = ANSWER_TEXT_LEFT + max(
        _natural(question, ANSWER_Q_SIZE),
        _natural(answer, ANSWER_A_SIZE),
    ) + ANSWER_PAD_RIGHT
    width = int(max(ANSWER_MIN_WIDTH, min(ANSWER_MAX_WIDTH, math.ceil(needed + 2))))
    text_width = max(40.0, width - ANSWER_TEXT_LEFT - ANSWER_PAD_RIGHT)

    q_lines = _wrap(question, ANSWER_Q_SIZE, text_width)[:3]
    a_lines = _wrap(answer, ANSWER_A_SIZE, text_width)[:7]

    max_line = 0.0
    for line in q_lines:
        max_line = max(max_line, _line_width(line, ANSWER_Q_SIZE))
    for line in a_lines:
        max_line = max(max_line, _line_width(line, ANSWER_A_SIZE))
    width = int(
        max(
            ANSWER_MIN_WIDTH,
            min(
                ANSWER_MAX_WIDTH,
                math.ceil(ANSWER_TEXT_LEFT + max_line + ANSWER_PAD_RIGHT + 2),
            ),
        )
    )
    text_width = max(40.0, width - ANSWER_TEXT_LEFT - ANSWER_PAD_RIGHT)
    q_lines = _wrap(question, ANSWER_Q_SIZE, text_width)[:3]
    a_lines = _wrap(answer, ANSWER_A_SIZE, text_width)[:7]

    last_baseline = (
        ANSWER_TOP
        + len(q_lines) * ANSWER_Q_LINE
        + ANSWER_GAP
        + max(0, len(a_lines) - 1) * ANSWER_A_LINE
    )
    text_h = int(max(ANSWER_TEXT_MIN_HEIGHT, math.ceil(last_baseline + ANSWER_TEXT_BOTTOM_PAD)))
    return width, text_h



def _surface_xid(surface) -> int | None:
    for getter in (
        lambda: surface.get_xid(),
        lambda: __import__("gi.repository", fromlist=["GdkX11"]).GdkX11.X11Surface.get_xid(surface),
    ):
        try:
            return int(getter())
        except Exception:
            continue
    return None


class _X11:
    """Minimal X11 helpers — python-xlib preferred, ctypes fallback.

    Holds one display connection and caches the resolved toplevel per xid, so
    a drag costs one ConfigureWindow + flush per frame instead of a fresh
    connection, a tree walk and a round-trip sync per motion event.
    """

    def __init__(self) -> None:
        self._mode = "none"
        self._xlib = None
        self._lib = None
        self._dpy = None
        self._top: dict[int, Any] = {}
        self._prepared: set[int] = set()
        try:
            from Xlib import X, display

            self._xlib = (display, X)
            self._mode = "xlib"
        except Exception:
            try:
                lib = ctypes.CDLL("libX11.so.6")
                lib.XOpenDisplay.restype = ctypes.c_void_p
                lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
                lib.XDefaultRootWindow.restype = ctypes.c_ulong
                lib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
                lib.XMoveWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int]
                lib.XRaiseWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
                lib.XMapRaised.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
                lib.XFlush.argtypes = [ctypes.c_void_p]
                lib.XCloseDisplay.argtypes = [ctypes.c_void_p]
                lib.XQueryTree.argtypes = [
                    ctypes.c_void_p,
                    ctypes.c_ulong,
                    ctypes.POINTER(ctypes.c_ulong),
                    ctypes.POINTER(ctypes.c_ulong),
                    ctypes.POINTER(ctypes.POINTER(ctypes.c_ulong)),
                    ctypes.POINTER(ctypes.c_uint),
                ]
                lib.XQueryTree.restype = ctypes.c_int
                lib.XQueryPointer.argtypes = [
                    ctypes.c_void_p,
                    ctypes.c_ulong,
                    ctypes.POINTER(ctypes.c_ulong),
                    ctypes.POINTER(ctypes.c_ulong),
                    ctypes.POINTER(ctypes.c_int),
                    ctypes.POINTER(ctypes.c_int),
                    ctypes.POINTER(ctypes.c_int),
                    ctypes.POINTER(ctypes.c_int),
                    ctypes.POINTER(ctypes.c_uint),
                ]
                lib.XQueryPointer.restype = ctypes.c_int
                lib.XFree.argtypes = [ctypes.c_void_p]
                self._lib = lib
                self._mode = "ctypes"
            except Exception:
                self._mode = "none"

    @property
    def ok(self) -> bool:
        return self._mode != "none"

    def _display(self):
        if self._dpy is not None:
            return self._dpy
        if self._mode == "xlib":
            display, _X = self._xlib
            self._dpy = display.Display()
        elif self._mode == "ctypes":
            dpy = self._lib.XOpenDisplay(None)
            self._dpy = dpy or None
        return self._dpy

    def _reset(self) -> None:
        dpy, self._dpy = self._dpy, None
        self._top.clear()
        self._prepared.clear()
        if dpy is None:
            return
        try:
            if self._mode == "xlib":
                dpy.close()
            elif self._mode == "ctypes":
                self._lib.XCloseDisplay(dpy)
        except Exception:
            pass

    def forget(self) -> None:
        """Re-resolve the toplevel next time (WM may have re-framed the window)."""
        self._top.clear()
        self._prepared.clear()

    def _toplevel(self, xid: int):
        top = self._top.get(int(xid))
        if top is not None:
            return top
        dpy = self._display()
        if dpy is None:
            return None
        if self._mode == "xlib":
            top = self._toplevel_xlib(dpy, xid)
        else:
            top = self._toplevel_ctypes(self._lib, dpy, int(xid))
        self._top[int(xid)] = top
        return top

    def prepare(self, xid: int) -> bool:
        if int(xid) in self._prepared:
            return True
        if self._mode == "xlib":
            try:
                win = self._toplevel(xid)
                win.change_attributes(override_redirect=1)
                try:
                    win.map()
                except Exception:
                    pass
                self._dpy.sync()
                self._prepared.add(int(xid))
                return True
            except Exception:
                self._reset()
                return False
        if self._mode == "ctypes":
            # Best-effort: move/raise still work without override-redirect.
            self._prepared.add(int(xid))
            return True
        return False

    def move(self, xid: int, x: int, y: int) -> bool:
        if self._mode == "xlib":
            _display, X = self._xlib
            try:
                win = self._toplevel(xid)
                if win is None:
                    return False
                win.configure(x=int(x), y=int(y), stack_mode=X.Above)
                self._dpy.flush()
                return True
            except Exception:
                self._reset()
                return False
        if self._mode == "ctypes":
            try:
                top = self._toplevel(xid)
                if top is None:
                    return False
                lib = self._lib
                lib.XMoveWindow(self._dpy, top, int(x), int(y))
                lib.XRaiseWindow(self._dpy, top)
                lib.XMapRaised(self._dpy, top)
                lib.XFlush(self._dpy)
                return True
            except Exception:
                self._reset()
                return False
        return False

    def geometry(self, xid: int) -> tuple[int, int] | None:
        """Root position of the toplevel we move (xlib only)."""
        if self._mode != "xlib":
            return None
        try:
            win = self._toplevel(xid)
            root = self._dpy.screen().root
            coords = root.translate_coords(win, 0, 0)
            return int(coords.x), int(coords.y)
        except Exception:
            self._reset()
            return None

    def pointer(self) -> tuple[int, int] | None:
        """Pointer position in root coordinates."""
        dpy = self._display() if self.ok else None
        if dpy is None:
            return None
        if self._mode == "xlib":
            try:
                reply = dpy.screen().root.query_pointer()
                return int(reply.root_x), int(reply.root_y)
            except Exception:
                self._reset()
                return None
        lib = self._lib
        root = lib.XDefaultRootWindow(dpy)
        r, c = ctypes.c_ulong(), ctypes.c_ulong()
        rx, ry, wx, wy = ctypes.c_int(), ctypes.c_int(), ctypes.c_int(), ctypes.c_int()
        mask = ctypes.c_uint()
        if not lib.XQueryPointer(
            dpy, root, ctypes.byref(r), ctypes.byref(c), ctypes.byref(rx),
            ctypes.byref(ry), ctypes.byref(wx), ctypes.byref(wy), ctypes.byref(mask),
        ):
            return None
        return int(rx.value), int(ry.value)

    def primary_output_name(self) -> str | None:
        """RandR primary output name (e.g. 'eDP-1'), matching GdkMonitor connector."""
        if self._mode != "xlib":
            return None
        try:
            dpy = self._display()
            root = dpy.screen().root
            out = dpy.xrandr_get_output_primary(root).output
            if not out:
                return None
            return str(dpy.xrandr_get_output_info(out, 0).name)
        except Exception:
            return None
    def _toplevel_xlib(self, dpy, xid: int):
        root = dpy.screen().root
        win = dpy.create_resource_object("window", int(xid))
        for _ in range(8):
            tree = win.query_tree()
            parent = tree.parent
            if parent is None or parent.id == root.id:
                return win
            try:
                ptree = parent.query_tree()
                if ptree.parent is not None and ptree.parent.id == root.id:
                    return parent
            except Exception:
                pass
            win = parent
        return win

    def _toplevel_ctypes(self, lib, dpy, xid: int) -> int:
        root = lib.XDefaultRootWindow(dpy)
        cur = int(xid)
        for _ in range(8):
            root_ret = ctypes.c_ulong()
            parent = ctypes.c_ulong()
            children = ctypes.POINTER(ctypes.c_ulong)()
            n = ctypes.c_uint()
            if not lib.XQueryTree(
                dpy, cur, ctypes.byref(root_ret), ctypes.byref(parent),
                ctypes.byref(children), ctypes.byref(n),
            ):
                break
            if children:
                lib.XFree(children)
            if parent.value == 0 or parent.value == root:
                return cur
            # parent is frame under root → use parent
            root2 = ctypes.c_ulong()
            parent2 = ctypes.c_ulong()
            children2 = ctypes.POINTER(ctypes.c_ulong)()
            n2 = ctypes.c_uint()
            if lib.XQueryTree(
                dpy, parent.value, ctypes.byref(root2), ctypes.byref(parent2),
                ctypes.byref(children2), ctypes.byref(n2),
            ):
                if children2:
                    lib.XFree(children2)
                if parent2.value == root:
                    return int(parent.value)
            cur = int(parent.value)
        return cur


_X11_SINGLETON: _X11 | None = None


def _x11() -> _X11:
    global _X11_SINGLETON
    if _X11_SINGLETON is None:
        _X11_SINGLETON = _X11()
        print(f"[vaani] indicator x11 backend={_X11_SINGLETON._mode}", flush=True)
    return _X11_SINGLETON


def run_gtk(
    *,
    amplitude_path: Path,
    control_path: Path,
    position_path: Path,
    phase_path: Path,
    answer_path: Path | None = None,
) -> int:
    import cairo
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    from gi.repository import Gdk, GLib, Gtk

    try:
        # Loads the X11 backend types so the display exposes get_primary_monitor().
        gi.require_version("GdkX11", "4.0")
        from gi.repository import GdkX11  # noqa: F401
    except Exception:
        pass

    from ...indicator_protocol import (
        read_answer,
        read_phase,
        read_session,
        resolve_answer_path,
        resolve_session_path,
        write_command,
    )
    from ...waveform import WaveformBuffer

    x11 = _x11()
    wave = WaveformBuffer(bars=BAR_COUNT)
    answer_file = answer_path if answer_path is not None else resolve_answer_path()
    session_file = resolve_session_path()
    # NON_UNIQUE: a second pill process must not "activate" this one — that used
    # to spawn an extra window here with its own stale state (old card text
    # drawn inside the pill). One process ⇒ exactly one window.
    from gi.repository import Gio

    app = Gtk.Application(
        application_id="com.vaani.RecordingIndicator", flags=Gio.ApplicationFlags.NON_UNIQUE
    )
    windows: list = []
    P = SHADOW_PAD

    def activate(application: Gtk.Application) -> None:
        if windows:
            return
        win = Gtk.Window(application=application)
        windows.append(win)
        win.set_title("Vaani")
        win.set_decorated(False)
        win.set_resizable(False)
        win.set_default_size(WIDTH + 2 * P, HEIGHT + 2 * P)

        css = Gtk.CssProvider()
        css.load_from_data(
            b"""
            window, window.background, drawingarea {
              background-color: rgba(0,0,0,0);
              background-image: none;
              border: none;
              box-shadow: none;
            }
            """
        )
        display = Gdk.Display.get_default()
        Gtk.StyleContext.add_provider_for_display(
            display, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        area = Gtk.DrawingArea()
        area.set_content_width(WIDTH + 2 * P)
        area.set_content_height(HEIGHT + 2 * P)
        win.set_child(area)

        def monitors() -> tuple[list[str], list[Rect], int]:
            """(connector names, geometries, primary index) of current monitors."""
            names: list[str] = []
            rects: list[Rect] = []
            primary_mon = None
            try:
                primary_mon = display.get_primary_monitor()  # GdkX11.X11Display
            except Exception:
                primary_mon = None
            primary = 0
            try:
                model = display.get_monitors()
                for i in range(model.get_n_items()):
                    mon = model.get_item(i)
                    geo = mon.get_geometry()
                    if geo.width <= 0 or geo.height <= 0:
                        continue
                    if primary_mon is not None and mon == primary_mon:
                        primary = len(rects)
                    names.append(str(mon.get_connector() or f"mon{i}"))
                    rects.append((int(geo.x), int(geo.y), int(geo.width), int(geo.height)))
            except Exception:
                pass
            if not rects:
                return ["default"], [(0, 0, 1920, 1080)], 0
            if primary_mon is None:
                name = x11.primary_output_name()
                if name in names:
                    primary = names.index(name)
            return names, rects, primary

        def monitor_at(cx: float, cy: float) -> tuple[str, Rect]:
            names, rects, primary = monitors()
            i = _pick_monitor(rects, cx, cy, primary)
            return names[i], rects[i]

        names0, rects0, primary0 = monitors()
        layout = _load_layout(position_path)
        orient = "h"
        px = py = None
        saved_mon = layout.get("monitor")
        if saved_mon in names0 and "rx" in layout and "ry" in layout:
            rect = rects0[names0.index(saved_mon)]
            rx, ry = float(layout["rx"]), float(layout["ry"])
            orient = _zone_orientation(rect[1] + ry * rect[3], rect, "h")
            pw, ph = _pill_dims(orient)
            px, py = _pos_from_rel(rx, ry, pw, ph, rect)
        else:
            saved = _load_position(position_path)
            if saved is not None and not saved_mon:
                # Legacy absolute save (landscape pill).
                px, py = _clamp_to_monitors(saved[0], saved[1], WIDTH, HEIGHT, rects0, primary0)
                rect = rects0[_pick_monitor(rects0, px + WIDTH / 2, py + HEIGHT / 2, primary0)]
                orient = _zone_orientation(py + HEIGHT / 2, rect, "h")
                if orient == "v":
                    px, py = _clamp_to_monitors(
                        px + (WIDTH - HEIGHT) // 2, py - (WIDTH - HEIGHT) // 2,
                        HEIGHT, WIDTH, rects0, primary0,
                    )
        if px is None or py is None:
            # First run, or the saved monitor is gone: primary, bottom-center.
            orient = "h"
            px, py = _default_pos(*rects0[primary0])
        pw0, ph0 = _pill_dims(orient)

        state: dict[str, object] = {
            "x": px,
            "y": py,
            "w": pw0,
            "h": ph0,
            "orient": orient,
            "monitor_sig": (tuple(names0), tuple(rects0)),
            # Remembered spot (monitor connector + relative center). Restored on
            # hotplug; only user placement changes it, so a pill bumped to the
            # primary monitor returns when its display comes back.
            "spot": _spot_for_pos(px, py, pw0, ph0, names0, rects0, primary0),
            "drag": None,  # active press/drag bookkeeping
            "held": None,  # button under a held press ("left" | "right")
            "moves_ok": 0,
            "answer_active": False,
            "options": [],
            "option_hit": None,  # (y0, line_h, count) while clarify is shown
            "hover": False,
            "dismiss_id": None,
            "question": "",
            "answer": "",
            "anim_t0": 0.0,
            "anim_from": None,
            "anim_to": None,
            "anim_progress": 1.0,
            "anim_mode": "expand",
            # Persistent pill: hidden while the daemon phase is "idle", or after a
            # local dismiss until the daemon moves to a different phase.
            "hidden": read_phase(phase_path) == "idle",
            "hidden_phase": None,
            "press": None,  # (button, monotonic t) — "left" | "right" | ("option", i)
            "hover_btn": None,
            "session": read_session(session_file),
            "shown_at": time.monotonic(),
        }

        def size() -> tuple[int, int]:
            return int(state["w"]), int(state["h"])

        def pill_size() -> tuple[int, int]:
            return _pill_dims(str(state["orient"]))

        def set_content_size(w: int, h: int) -> None:
            w, h = max(1, int(w)), max(1, int(h))
            state["w"], state["h"] = w, h
            win.set_default_size(w + 2 * P, h + 2 * P)
            area.set_content_width(w + 2 * P)
            area.set_content_height(h + 2 * P)
            try:
                win.set_size_request(w + 2 * P, h + 2 * P)
            except Exception:
                pass

        def apply_chrome(surface) -> None:
            w, h = size()
            try:
                if hasattr(surface, "set_opaque_region"):
                    surface.set_opaque_region(None)
            except Exception:
                pass
            try:
                if hasattr(surface, "set_input_region"):
                    region = (
                        cairo.Region()
                        if state.get("hidden")
                        else cairo.Region(cairo.RectangleInt(P, P, w, h))
                    )
                    surface.set_input_region(region)
            except Exception:
                pass

        def _xid():
            surface = win.get_surface()
            if surface is None:
                return None, None
            return surface, _surface_xid(surface)

        def place(x: int | None = None, y: int | None = None) -> bool:
            surface, xid = _xid()
            if surface is None:
                return False
            apply_chrome(surface)
            if xid is None:
                print("[vaani] indicator: no xid", flush=True)
                return False
            x11.prepare(xid)
            if state.get("hidden"):
                return x11.move(xid, OFFSCREEN[0], OFFSCREEN[1])
            _names, rects, primary = monitors()
            w, h = size()
            px_ = int(state["x"] if x is None else x)
            py_ = int(state["y"] if y is None else y)
            px_, py_ = _clamp_to_monitors(px_, py_, w, h, rects, primary)
            state["x"], state["y"] = px_, py_
            ok = x11.move(xid, px_ - P, py_ - P)
            if ok:
                state["moves_ok"] = int(state["moves_ok"]) + 1
            else:
                print(f"[vaani] indicator: move failed target={px_},{py_}", flush=True)
            return ok

        def persist_position() -> None:
            # Never persist the expanded answer-card geometry — save the pill
            # spot it grew from.
            if state.get("answer_active"):
                anchor = state.get("record_anchor")
                if not (isinstance(anchor, tuple) and len(anchor) == 2):
                    return
                px_, py_ = int(anchor[0]), int(anchor[1])
            else:
                px_, py_ = int(state["x"]), int(state["y"])
            pw, ph = pill_size()
            # Ignore bogus top-left saves if we never successfully moved.
            if px_ <= 2 and py_ <= 2 and int(state["moves_ok"]) == 0:
                return
            names, rects, primary = monitors()
            spot = _spot_for_pos(px_, py_, pw, ph, names, rects, primary)
            state["spot"] = spot
            _save_position(position_path, px_, py_, **spot)

        def set_orientation(new: str, *, pivot: tuple[float, float] | None = None) -> None:
            """Swap landscape/portrait around ``pivot`` (default: pill center)."""
            if new == state["orient"] or state.get("answer_active"):
                return
            w, h = size()
            cx, cy = pivot if pivot is not None else (
                int(state["x"]) + w / 2.0, int(state["y"]) + h / 2.0
            )
            state["orient"] = new
            nw, nh = pill_size()
            set_content_size(nw, nh)
            state["x"], state["y"] = int(round(cx - nw / 2.0)), int(round(cy - nh / 2.0))
            # Replay the spring-open so the swap reads as intentional.
            state["shown_at"] = time.monotonic()
            place()

        def relayout_for_monitors() -> None:
            """Display hotplug / resolution change: restore the saved spot."""
            names, rects, primary = monitors()
            if state.get("answer_active"):
                _reset_to_pill()
            spot = state.get("spot") if isinstance(state.get("spot"), dict) else {}
            rect, rx, ry = _resolve_spot(spot, names, rects, primary, *pill_size())
            orient_ = _zone_orientation(rect[1] + ry * rect[3], rect, str(state["orient"]))
            state["orient"] = orient_
            pw, ph = pill_size()
            set_content_size(pw, ph)
            state["x"], state["y"] = _pos_from_rel(rx, ry, pw, ph, rect)
            _surface, xid = _xid()
            actual = x11.geometry(xid) if xid is not None else None
            print(
                f"[vaani] indicator monitors={list(zip(names, rects))} primary={primary} "
                f"spot={spot} target={state['x']},{state['y']} orient={orient_} "
                f"window_was={actual}",
                flush=True,
            )
            x11.forget()
            for delay in (0, 60, 200):
                GLib.timeout_add(delay, lambda: (place(), False)[1])

        def check_monitors() -> None:
            names, rects, _primary = monitors()
            sig = (tuple(names), tuple(rects))
            if sig == state.get("monitor_sig"):
                return
            state["monitor_sig"] = sig
            relayout_for_monitors()

        def guard_position() -> None:
            """Re-place if the WM moved us (e.g. dropped at 0,0 after a RandR event)."""
            if state.get("hidden") or float(state.get("anim_progress", 1.0)) < 1.0:
                return
            _surface, xid = _xid()
            if xid is None:
                return
            actual = x11.geometry(xid)
            want = (int(state["x"]) - P, int(state["y"]) - P)
            if actual is None or (abs(actual[0] - want[0]) <= 1 and abs(actual[1] - want[1]) <= 1):
                return
            print(f"[vaani] indicator: window at {actual}, expected {want}; re-placing", flush=True)
            x11.forget()
            place()

        def _cancel_dismiss_timer() -> None:
            dismiss_id = state.get("dismiss_id")
            if dismiss_id is not None:
                try:
                    GLib.source_remove(int(dismiss_id))
                except Exception:
                    pass
                state["dismiss_id"] = None

        def _reset_to_pill() -> None:
            """Drop any answer-card state and snap back to pill geometry."""
            _cancel_dismiss_timer()
            state["options"] = []
            state["option_hit"] = None
            state["anim_t0"] = 0.0
            state["anim_from"] = state["anim_to"] = None
            anchor = state.get("record_anchor")
            if state.get("answer_active") and isinstance(anchor, tuple) and len(anchor) == 2:
                state["x"], state["y"] = int(anchor[0]), int(anchor[1])
            _finish_collapse_to_recording()

        def _hide(until_phase_changes: str | None = None) -> None:
            if state.get("answer_active"):
                try:
                    persist_position()
                except Exception:
                    pass
            state["hidden_phase"] = until_phase_changes
            if state.get("hidden"):
                return
            # Mark hidden first: _reset_to_pill() calls place(), which must
            # park offscreen rather than flash the pill for one frame.
            state["hidden"] = True
            _reset_to_pill()
            state["press"] = None
            state["hover_btn"] = None
            state["drag"] = None
            state["held"] = None
            place()
            area.queue_draw()

        def _show() -> None:
            if not state.get("hidden"):
                return
            state["hidden"] = False
            state["hidden_phase"] = None
            state["shown_at"] = time.monotonic()
            state["session"] = read_session(session_file)
            state["click_armed_at"] = time.monotonic() + CLICK_GRACE_S
            x11.forget()
            # Mutter can ignore the first configure — same retries as startup.
            for delay in (0, 40, 120):
                GLib.timeout_add(delay, lambda: (place(), False)[1])

        def _dismiss_answer_ui() -> None:
            _cancel_dismiss_timer()
            _hide(until_phase_changes=read_phase(phase_path))

        def _dismiss_answer() -> bool:
            state["dismiss_id"] = None
            _dismiss_answer_ui()
            return False

        def _schedule_dismiss(ms: int) -> None:
            _cancel_dismiss_timer()
            state["dismiss_id"] = GLib.timeout_add(int(ms), _dismiss_answer)

        def _answer_progress() -> float:
            t0 = float(state.get("anim_t0") or 0.0)
            if t0 <= 0:
                return 1.0
            raw = (time.monotonic() - t0) / (ANSWER_ANIM_MS / 1000.0)
            return _ease_smooth(raw)

        def _apply_answer_geometry(progress: float) -> None:
            fr = state.get("anim_from")
            to = state.get("anim_to")
            if not isinstance(fr, tuple) or not isinstance(to, tuple):
                return
            p = max(0.0, min(1.0, float(progress)))
            aw = int(round(fr[0] + (to[0] - fr[0]) * p))
            ah = int(round(fr[1] + (to[1] - fr[1]) * p))
            ax = int(round(fr[2] + (to[2] - fr[2]) * p))
            ay = int(round(fr[3] + (to[3] - fr[3]) * p))
            set_content_size(aw, ah)
            state["x"], state["y"] = ax, ay
            state["anim_progress"] = p
            place(ax, ay)

        def _finish_collapse_to_recording() -> None:
            state["answer_active"] = False
            state["question"] = ""
            state["answer"] = ""
            state["anim_mode"] = "expand"
            state["anim_progress"] = 1.0
            set_content_size(*pill_size())
            place()

        def _begin_collapse_to_recording() -> None:
            """Reverse-morph answer card back into the recording pill."""
            if state.get("anim_mode") == "collapse":
                return
            _cancel_dismiss_timer()
            old_w, old_h = size()
            old_x, old_y = int(state["x"]), int(state["y"])
            pw, ph = pill_size()
            anchor = state.get("record_anchor")
            if isinstance(anchor, tuple) and len(anchor) == 2:
                nx, ny = int(anchor[0]), int(anchor[1])
            else:
                _names, rects, primary = monitors()
                nx, ny = _default_pos(*rects[primary], w=pw, h=ph)
            _names, rects, primary = monitors()
            nx, ny = _clamp_to_monitors(nx, ny, pw, ph, rects, primary)
            state["anim_from"] = (old_w, old_h, old_x, old_y)
            state["anim_to"] = (pw, ph, nx, ny)
            state["anim_t0"] = time.monotonic()
            state["anim_progress"] = 0.0
            state["anim_mode"] = "collapse"
            state["answer_active"] = True  # keep drawing text until fade completes
            _apply_answer_geometry(0.0)

        def _answer_mtime() -> float:
            try:
                return os.stat(answer_file).st_mtime_ns / 1e9
            except OSError:
                return 0.0

        def _card_target(aw: int, ah: int) -> tuple[int, int]:
            anchor = state.get("record_anchor")
            ax, ay = (
                anchor if isinstance(anchor, tuple) and len(anchor) == 2
                else (int(state["x"]), int(state["y"]))
            )
            pw, ph = pill_size()
            _name, rect = monitor_at(int(ax) + pw / 2.0, int(ay) + ph / 2.0)
            return _answer_anchor_pos(int(ax), int(ay), pw, ph, aw, ah, rect)

        def _refresh_answer_payload() -> None:
            """Card already open and its text changed (task progress, confirm step)."""
            mtime = _answer_mtime()
            if not mtime or mtime == state.get("answer_mtime"):
                return
            if state.get("anim_mode") == "collapse" or float(state.get("anim_progress") or 0) < 1.0:
                return
            state["answer_mtime"] = mtime
            payload = read_answer(answer_file)
            state["question"] = str(payload.get("question") or "")
            state["answer"] = str(payload.get("answer") or "")
            opts = payload.get("options") if isinstance(payload.get("options"), list) else []
            state["options"] = [str(item) for item in opts[:5] if str(item).strip()]
            state["countdown"] = (
                (float(payload["deadline"]), float(payload["countdown_s"]))
                if payload.get("deadline") and payload.get("countdown_s")
                else None
            )
            aw, ah = _measure_answer_size(state["question"], state["answer"], cairo_mod=cairo)
            nx, ny = _card_target(aw, ah)
            old_w, old_h = size()
            state["anim_from"] = (old_w, old_h, int(state["x"]), int(state["y"]))
            state["anim_to"] = (aw, ah, nx, ny)
            state["anim_t0"] = time.monotonic()
            state["anim_progress"] = 0.0
            state["anim_mode"] = "resize"
            _cancel_dismiss_timer()
            if not state["hover"]:
                _schedule_dismiss(
                    (ANSWER_CLARIFY_DISMISS_MS if state["options"] else ANSWER_AUTO_DISMISS_MS) + ANSWER_ANIM_MS
                )

        def _enter_answer_phase() -> None:
            if state["answer_active"] and state.get("anim_mode") != "collapse":
                return
            if state.get("anim_mode") == "collapse":
                return
            payload = read_answer(answer_file)
            state["answer_mtime"] = _answer_mtime()
            state["question"] = str(payload.get("question") or "")
            state["answer"] = str(payload.get("answer") or "")
            opts = payload.get("options") if isinstance(payload.get("options"), list) else []
            state["options"] = [str(item) for item in opts[:5] if str(item).strip()]
            state["countdown"] = (
                (float(payload["deadline"]), float(payload["countdown_s"]))
                if payload.get("deadline") and payload.get("countdown_s")
                else None
            )
            old_w, old_h = size()
            old_x, old_y = int(state["x"]), int(state["y"])
            aw, ah = _measure_answer_size(
                state["question"], state["answer"], cairo_mod=cairo
            )
            # Grow away from the nearer screen edge, centered on the pill.
            state["record_anchor"] = (old_x, old_y)
            nx, ny = _card_target(aw, ah)
            state["anim_from"] = (old_w, old_h, old_x, old_y)
            state["anim_to"] = (aw, ah, nx, ny)
            state["anim_t0"] = time.monotonic()
            state["anim_progress"] = 0.0
            state["anim_mode"] = "expand"
            state["answer_active"] = True
            _apply_answer_geometry(0.0)
            if not state["hover"]:
                dismiss_ms = (
                    ANSWER_CLARIFY_DISMISS_MS
                    if state["options"]
                    else ANSWER_AUTO_DISMISS_MS
                )
                _schedule_dismiss(dismiss_ms + ANSWER_ANIM_MS)

        def _press_progress(button: Any) -> float | None:
            """0..1 through the press animation for ``button``, else None."""
            press = state.get("press")
            if not press or press[0] != button:
                return None
            k = (time.monotonic() - float(press[1])) / (PRESS_ANIM_MS / 1000.0)
            if k >= 1.0:
                return None
            return max(0.0, k)

        def _draw_button_feedback(cr, bx: float, cy: float, r: float, button: str, a: float) -> float:
            """Hover glow, held squeeze, release halo. Returns the icon scale."""
            k = _press_progress(button)
            held = state.get("held") == button
            if (state.get("hover_btn") == button or held) and k is None:
                glow = cairo.RadialGradient(bx, cy, r * 0.6, bx, cy, r + 5.0)
                glow.add_color_stop_rgba(0.0, 1, 1, 1, (0.16 if held else 0.10) * a)
                glow.add_color_stop_rgba(1.0, 1, 1, 1, 0.0)
                cr.set_source(glow)
                cr.arc(bx, cy, r + 5.0, 0, 2 * math.pi)
                cr.fill()
            if held:
                return 0.86
            if k is None:
                return 1.0
            cr.set_source_rgba(1, 1, 1, 0.40 * (1.0 - k) * a)
            cr.set_line_width(1.6)
            cr.arc(bx, cy, r + 1.5 + 7.0 * k, 0, 2 * math.pi)
            cr.stroke()
            return 1.0 - 0.18 * math.sin(math.pi * k)

        def _accent() -> tuple[float, float, float]:
            sess = state.get("session") or {}
            return ASSISTANT_ACCENT if sess.get("mode") == "assistant" else DICTATION_ACCENT

        def _elapsed() -> float:
            sess = state.get("session") or {}
            started = float(sess.get("started") or 0.0)
            return max(0.0, time.time() - started) if started > 0 else 0.0

        def _warn_level() -> int:
            """0 normal, 1 last minute, 2 last 15s."""
            sess = state.get("session") or {}
            max_s = float(sess.get("max_s") or 0.0)
            warn_s = float(sess.get("warn_s") or 0.0)
            if max_s <= 0 or warn_s <= 0:
                return 0
            el = _elapsed()
            if el >= max_s - 15.0:
                return 2
            if el >= warn_s:
                return 1
            return 0

        def _rounded_rect(cr, x: float, y: float, w: float, h: float, r: float) -> None:
            r = max(0.0, min(r, h / 2.0, w / 2.0))
            cr.new_sub_path()
            cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
            cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
            cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
            cr.arc(x + r, y + r, r, math.pi, 1.5 * math.pi)
            cr.close_path()

        def _draw_glass(
            cr, x: float, y: float, w: float, h: float, r: float, a: float, border=None
        ) -> None:
            """Frosted-glass slab: shadow, tinted body, sheen, rim light, hairline."""
            if a <= 0.01 or w <= 1 or h <= 1:
                return
            r = max(0.0, min(r, w / 2.0, h / 2.0))
            accent = _accent()

            # Soft drop shadow outside the shape only (glass stays see-through).
            cr.save()
            cr.rectangle(x - P, y - P, w + 2 * P, h + 2 * P)
            _rounded_rect(cr, x, y, w, h, r)
            cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
            cr.clip()
            cr.set_fill_rule(cairo.FILL_RULE_WINDING)
            steps = 9
            for i in range(steps, 0, -1):
                s = i * 1.4
                _rounded_rect(cr, x - s, y - s + 3.0, w + 2 * s, h + 2 * s, r + s)
                cr.set_source_rgba(0, 0, 0, 0.030 * a)
                cr.fill()
            cr.restore()

            # Body: cool smoked glass, lighter at the top.
            _rounded_rect(cr, x, y, w, h, r)
            body = cairo.LinearGradient(0, y, 0, y + h)
            body.add_color_stop_rgba(0.0, *GLASS_TOP[:3], GLASS_TOP[3] * a)
            body.add_color_stop_rgba(1.0, *GLASS_BOTTOM[:3], GLASS_BOTTOM[3] * a)
            cr.set_source(body)
            cr.fill_preserve()
            # Faint accent glow pooled at the bottom center.
            tint = cairo.RadialGradient(x + w / 2.0, y + h, 0, x + w / 2.0, y + h, max(w, h) * 0.6)
            tint.add_color_stop_rgba(0.0, accent[0], accent[1], accent[2], 0.10 * a)
            tint.add_color_stop_rgba(1.0, accent[0], accent[1], accent[2], 0.0)
            cr.set_source(tint)
            cr.fill_preserve()

            # Specular sheen across the upper half.
            cr.save()
            cr.clip()
            sheen_h = h * 0.5 if h <= w else min(h * 0.5, w * 1.2)
            sheen = cairo.LinearGradient(0, y, 0, y + sheen_h)
            sheen.add_color_stop_rgba(0.0, 1, 1, 1, 0.16 * a)
            sheen.add_color_stop_rgba(1.0, 1, 1, 1, 0.015 * a)
            _rounded_rect(cr, x + 1.0, y + 1.0, w - 2.0, sheen_h, max(0.0, r - 1.0))
            cr.set_source(sheen)
            cr.fill()
            cr.restore()

            # Rim light: bright top edge, dim sides, soft bounce at the bottom.
            _rounded_rect(cr, x + 0.5, y + 0.5, w - 1.0, h - 1.0, max(0.0, r - 0.5))
            rim = cairo.LinearGradient(0, y, 0, y + h)
            rim.add_color_stop_rgba(0.0, 1, 1, 1, 0.42 * a)
            rim.add_color_stop_rgba(0.35, 1, 1, 1, 0.08 * a)
            rim.add_color_stop_rgba(0.75, 1, 1, 1, 0.05 * a)
            rim.add_color_stop_rgba(1.0, 1, 1, 1, 0.18 * a)
            cr.set_source(rim)
            cr.set_line_width(1.0)
            cr.stroke()
            # Dark hairline so the edge holds on bright backgrounds.
            _rounded_rect(cr, x - 0.5, y - 0.5, w + 1.0, h + 1.0, r + 0.5)
            cr.set_source_rgba(0, 0, 0, 0.30 * a)
            cr.set_line_width(1.0)
            cr.stroke()
            if border is not None:
                _rounded_rect(cr, x + 0.75, y + 0.75, w - 1.5, h - 1.5, max(0.0, r - 0.75))
                cr.set_source_rgba(border[0], border[1], border[2], border[3] * a)
                cr.set_line_width(1.5)
                cr.stroke()

        def _fmt(seconds: float) -> str:
            seconds = max(0, int(seconds))
            return f"{seconds // 60}:{seconds % 60:02d}"

        def _to_strip_x(ex: float, ey: float) -> float:
            """Content coords → position along the pill's long axis."""
            return ey if state.get("orient") == "v" else ex

        def _draw_control_strip(
            cr, width: int, height: int, *, phase: str, alpha: float = 1.0
        ) -> None:
            """Capsule: ✕ · waveform/shimmer (+ timer) · stop.

            Geometry is laid out landscape (``width`` × ``height``, strip at the
            bottom); a portrait pill rotates it 90° so ✕ sits on top.
            """
            if alpha <= 0.01:
                return
            portrait = state.get("orient") == "v" and not state.get("answer_active")
            a = max(0.0, min(1.0, float(alpha)))
            now = time.monotonic()
            # Appear: spring the capsule open from the centre.
            k = min(1.0, (now - float(state.get("shown_at") or 0.0)) / (APPEAR_MS / 1000.0))
            grow = 1.0 - (1.0 - k) ** 3
            a *= 0.35 + 0.65 * grow
            full_w = width - 2.0
            cap_w = full_w * (0.72 + 0.28 * grow)
            cap_h = HEIGHT - 2.0
            cap_x = (width - cap_w) / 2.0
            cap_y = height - HEIGHT + 1.0
            cy = cap_y + cap_h / 2.0
            accent = _accent()
            warn = _warn_level() if phase == "recording" else 0
            border = None
            if warn:
                col = DANGER_ACCENT if warn == 2 else WARN_ACCENT
                pulse = 0.55 + 0.45 * abs(math.sin(now * (5.0 if warn == 2 else 2.4)))
                border = (col[0], col[1], col[2], pulse)
            if portrait:
                # Landscape (lx, ly) → device (HEIGHT - ly, lx).
                _draw_glass(cr, HEIGHT - (cap_y + cap_h), cap_x, cap_h, cap_w, cap_h / 2.0, a, border)
            else:
                _draw_glass(cr, cap_x, cap_y, cap_w, cap_h, cap_h / 2.0, a, border)
            if grow < 0.6:
                return
            ca = a * min(1.0, (grow - 0.6) / 0.4)
            cr.save()
            if portrait:
                cr.translate(HEIGHT, 0)
                cr.rotate(math.pi / 2.0)

            # Left / top: cancel — small glass bead with an ✕.
            btn_r = 12.0
            lx = cap_x + 5.0 + btn_r
            sq = _draw_button_feedback(cr, lx, cy, btn_r, "left", ca)
            r_ = btn_r * sq
            bead = cairo.LinearGradient(0, cy - r_, 0, cy + r_)
            bead.add_color_stop_rgba(0.0, 1, 1, 1, 0.20 * ca)
            bead.add_color_stop_rgba(1.0, 1, 1, 1, 0.06 * ca)
            cr.set_source(bead)
            cr.arc(lx, cy, r_, 0, 2 * math.pi)
            cr.fill()
            cr.set_source_rgba(1, 1, 1, 0.22 * ca)
            cr.set_line_width(0.8)
            cr.arc(lx, cy, r_ - 0.4, math.pi * 1.1, math.pi * 1.9)
            cr.stroke()
            cr.set_source_rgba(0.95, 0.95, 0.97, 0.92 * ca)
            cr.set_line_width(1.6)
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            d = 3.6 * sq
            cr.move_to(lx - d, cy - d)
            cr.line_to(lx + d, cy + d)
            cr.move_to(lx + d, cy - d)
            cr.line_to(lx - d, cy + d)
            cr.stroke()

            # Right / bottom: stop (recording) / cancel-able spinner (processing).
            rx = cap_x + cap_w - 5.0 - btn_r
            sq = _draw_button_feedback(cr, rx, cy, btn_r, "right", ca)
            r_ = btn_r * sq
            if phase == "processing":
                cr.set_source_rgba(1, 1, 1, 0.09 * ca)
                cr.arc(rx, cy, r_, 0, 2 * math.pi)
                cr.fill()
                start = (now * 5.0) % (2 * math.pi)
                cr.set_source_rgba(accent[0], accent[1], accent[2], 0.18 * ca)
                cr.set_line_width(2.0)
                cr.arc(rx, cy, 6.0 * sq, 0, 2 * math.pi)
                cr.stroke()
                cr.set_source_rgba(accent[0], accent[1], accent[2], 0.95 * ca)
                cr.set_line_cap(cairo.LINE_CAP_ROUND)
                cr.arc(rx, cy, 6.0 * sq, start, start + math.pi * 1.25)
                cr.stroke()
            else:
                btn_col = accent
                if warn:
                    btn_col = DANGER_ACCENT if warn == 2 else WARN_ACCENT
                # Lit-from-above jewel button.
                jewel = cairo.RadialGradient(rx, cy - r_ * 0.7, 0, rx, cy, r_ * 1.15)
                lift = [min(1.0, c + 0.10) for c in btn_col]
                jewel.add_color_stop_rgba(0.0, lift[0], lift[1], lift[2], 0.98 * ca)
                jewel.add_color_stop_rgba(1.0, btn_col[0] * 0.80, btn_col[1] * 0.80, btn_col[2] * 0.80, 0.98 * ca)
                cr.set_source(jewel)
                cr.arc(rx, cy, r_, 0, 2 * math.pi)
                cr.fill()
                cr.set_source_rgba(1, 1, 1, 0.45 * ca)
                cr.set_line_width(0.8)
                cr.arc(rx, cy, r_ - 0.5, math.pi * 1.15, math.pi * 1.85)
                cr.stroke()
                cr.set_source_rgba(0.06, 0.06, 0.07, 0.92 * ca)
                side = 7.0 * sq
                _rounded_rect(cr, rx - side / 2, cy - side / 2, side, side, 1.8)
                cr.fill()

            # Timer: hands-free always; otherwise after TIMER_AFTER_S; countdown when warning.
            inner_left = lx + btn_r + 9.0
            inner_right = rx - btn_r - 9.0
            sess = state.get("session") or {}
            elapsed = _elapsed()
            label = None
            label_col = (0.78, 0.78, 0.82)
            if phase == "recording" and elapsed > 0:
                if warn:
                    remaining = float(sess.get("max_s") or 0.0) - elapsed
                    label = _fmt(remaining) if portrait else f"{_fmt(remaining)} left"
                    label_col = DANGER_ACCENT if warn == 2 else WARN_ACCENT
                elif sess.get("handsfree") or elapsed >= TIMER_AFTER_S:
                    label = _fmt(elapsed)
            if label:
                cr.select_font_face(MONO_FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD if warn else cairo.FONT_WEIGHT_NORMAL)
                cr.set_font_size(11.0 if portrait else 11.5)
                ext = cr.text_extents(label)
                cr.set_source_rgba(label_col[0], label_col[1], label_col[2], 0.95 * ca)
                if portrait:
                    # Keep the digits upright: counter-rotate around the slot.
                    slot = ext.height + 4.0
                    tcx = inner_right - slot / 2.0
                    cr.save()
                    cr.translate(tcx, cy)
                    cr.rotate(-math.pi / 2.0)
                    cr.move_to(-ext.x_advance / 2.0, ext.height / 2.0 - 0.5)
                    cr.show_text(label)
                    cr.restore()
                    inner_right -= slot + 4.0
                else:
                    tx = inner_right - ext.x_advance
                    cr.move_to(tx, cy + ext.height / 2.0 - 0.5)
                    cr.show_text(label)
                    inner_right = tx - 7.0
                if sess.get("handsfree") and not warn:
                    # Small "locked" dot = hands-free.
                    cr.set_source_rgba(accent[0], accent[1], accent[2], 0.9 * ca)
                    cr.arc(inner_right + 1.5, cy, 2.2, 0, 2 * math.pi)
                    cr.fill()
                    inner_right -= 7.0

            span = max(10.0, inner_right - inner_left)
            n = BAR_COUNT
            step = span / n
            bar_w = min(2.4, step * 0.55)
            max_h = cap_h - 16.0
            if phase == "processing":
                t = now * 1.6
                levels = []
                for i in range(n):
                    x = i / max(1, n - 1)
                    phase_x = (t % 1.6) / 1.6 * 1.4 - 0.2
                    bump = math.exp(-((x - phase_x) ** 2) / 0.012)
                    levels.append(0.14 + 0.5 * bump)
            else:
                levels = wave.bars_now()
                if len(levels) != n:
                    levels = (levels + [0.1] * n)[:n]
            for i, level in enumerate(levels):
                bx = inner_left + (i + 0.5) * step
                edge = 1.0 - abs((i / max(1, n - 1)) * 2.0 - 1.0)
                alpha_i = (0.35 + 0.65 * (edge ** 0.6)) * ca
                # Gentle curve: speech stays lively without every bar pinned at max.
                lv = max(0.0, min(1.0, float(level))) ** 1.35
                bar_h = max(2.6, lv * max_h)
                if lv > 0.08:
                    # Soft bloom behind louder bars.
                    gw = bar_w + 3.0
                    cr.set_source_rgba(accent[0], accent[1], accent[2], 0.16 * alpha_i * lv)
                    _rounded_rect(cr, bx - gw / 2, cy - bar_h / 2 - 1.5, gw, bar_h + 3.0, gw / 2)
                    cr.fill()
                cr.set_source_rgba(accent[0], accent[1], accent[2], 0.95 * alpha_i)
                _rounded_rect(cr, bx - bar_w / 2, cy - bar_h / 2, bar_w, bar_h, bar_w / 2)
                cr.fill()
            cr.restore()

        def draw(_area, cr, _aw: int, _ah: int) -> None:
            cr.set_operator(cairo.OPERATOR_SOURCE)
            cr.set_source_rgba(0, 0, 0, 0)
            cr.paint()
            cr.set_operator(cairo.OPERATOR_OVER)

            if state.get("hidden"):
                return
            cr.translate(P, P)
            width, height = size()
            phase = read_phase(phase_path)
            if (
                phase in {"recording", "processing"}
                and state.get("answer_active")
                and state.get("anim_mode") != "collapse"
            ):
                # Stale card while a session runs: show the pill, not old text.
                _reset_to_pill()
                width, height = size()
            if phase == "answer" or state.get("answer_active"):
                progress = _answer_progress()
                collapsing = state.get("anim_mode") == "collapse"
                if state.get("anim_mode") == "resize":
                    # Card text changed in place: keep text solid while it resizes.
                    chrome_a, text_a, fill_a, radius = 0.0, 1.0, 1.0, ANSWER_RADIUS
                elif collapsing:
                    chrome_a = progress
                    text_a = max(0.0, 1.0 - progress)
                    fill_a = 1.0 - 0.75 * progress
                    radius = ANSWER_RADIUS + (HEIGHT / 2.0 - ANSWER_RADIUS) * progress
                else:
                    chrome_a = max(0.0, 1.0 - progress)
                    text_a = progress
                    fill_a = 0.25 + 0.75 * progress
                    radius = HEIGHT / 2.0 + (ANSWER_RADIUS - HEIGHT / 2.0) * progress
                if state.get("orient") == "v":
                    # Portrait pill ↔ landscape card: no squashed strip mid-morph.
                    chrome_a = 0.0

                _draw_glass(cr, 0.0, 0.0, float(width), float(height), radius, fill_a)

                if text_a > 0.02:
                    text_left = ANSWER_TEXT_LEFT
                    text_width = max(40.0, width - text_left - ANSWER_PAD_RIGHT)
                    cr.select_font_face(
                        FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL
                    )
                    cr.set_font_size(ANSWER_Q_SIZE)
                    cr.set_source_rgba(0.76, 0.78, 0.83, 0.95 * text_a)
                    q_lines = _wrap_cairo_text(
                        cr, str(state.get("question") or ""), text_width
                    )
                    y = ANSWER_TOP
                    for line in q_lines[:3]:
                        cr.move_to(text_left, y)
                        cr.show_text(line)
                        y += ANSWER_Q_LINE

                    sep_y = y + ANSWER_GAP * 0.35 - 0.5
                    sep = cairo.LinearGradient(text_left, 0, width - ANSWER_PAD_RIGHT, 0)
                    sep.add_color_stop_rgba(0.0, 1, 1, 1, 0.0)
                    sep.add_color_stop_rgba(0.5, 1, 1, 1, 0.16 * text_a)
                    sep.add_color_stop_rgba(1.0, 1, 1, 1, 0.0)
                    cr.set_source(sep)
                    cr.rectangle(text_left, sep_y, width - ANSWER_PAD_RIGHT - text_left, 1.0)
                    cr.fill()

                    cr.set_font_size(ANSWER_A_SIZE)
                    a_lines = _wrap_cairo_text(
                        cr, str(state.get("answer") or ""), text_width
                    )
                    y += ANSWER_GAP
                    option_y0 = y
                    press = state.get("press")
                    if press and isinstance(press[0], tuple):
                        k = _press_progress(press[0])
                        if k is not None:
                            row = int(press[0][1])
                            ry = option_y0 - ANSWER_A_LINE * 0.78 + row * ANSWER_A_LINE
                            cr.set_source_rgba(1, 1, 1, 0.16 * (1.0 - 0.5 * k) * text_a)
                            _rounded_rect(
                                cr, text_left - 6, ry, text_width + 12, ANSWER_A_LINE, 6.0
                            )
                            cr.fill()
                    cr.set_source_rgba(0.96, 0.97, 0.99, 0.98 * text_a)
                    for line in a_lines[:7]:
                        cr.move_to(text_left, y)
                        cr.show_text(line)
                        y += ANSWER_A_LINE
                    opts = state.get("options") or []
                    if opts:
                        state["option_hit"] = (
                            option_y0 - ANSWER_A_LINE * 0.7,
                            ANSWER_A_LINE,
                            min(len(opts), len(a_lines[:7])),
                        )
                    else:
                        state["option_hit"] = None
                    countdown = state.get("countdown")
                    if countdown:
                        deadline, total = countdown
                        frac = max(0.0, min(1.0, (deadline - time.time()) / max(0.1, total)))
                        bar_x, bar_w = radius, max(0.0, width - 2 * radius)
                        bar_y = height - 6.0
                        cr.set_source_rgba(1, 1, 1, 0.10 * text_a)
                        _rounded_rect(cr, bar_x, bar_y, bar_w, 3.0, 1.5)
                        cr.fill()
                        acc = _accent()
                        cr.set_source_rgba(acc[0], acc[1], acc[2], 0.9 * text_a)
                        _rounded_rect(cr, bar_x, bar_y, bar_w * frac, 3.0, 1.5)
                        cr.fill()

                if chrome_a > 0.02:
                    # Expand: chrome fades out. Collapse: chrome fades back in.
                    _draw_control_strip(
                        cr, width, height, phase="recording", alpha=chrome_a
                    )
                return

            # Recording / processing: the glass pill.
            if state.get("orient") == "v":
                _draw_control_strip(cr, WIDTH, HEIGHT, phase=phase)
            else:
                _draw_control_strip(cr, width, height, phase=phase)

        area.set_draw_func(draw)

        def _request(action: str) -> None:
            # Command first — never let position save block cancel/stop.
            try:
                write_command(control_path, action)
            except Exception as exc:
                print(f"[vaani] indicator: write_command({action}) failed: {exc!r}", flush=True)
            # Backup path: daemon listens for SIGUSR1/2 even if the file poll lags.
            try:
                import signal as _signal

                pid = int(os.environ.get("VAANI_DAEMON_PID") or os.getppid())
                sig = _signal.SIGUSR2 if action == "cancel" else _signal.SIGUSR1
                if pid > 1:
                    os.kill(pid, sig)
            except Exception:
                pass
            try:
                persist_position()
            except Exception:
                pass
            # Stay visible: the daemon's next phase (processing / idle) drives the
            # pill. Only hide locally if the daemon never reacts.
            clicked_phase = read_phase(phase_path)

            def _fallback() -> bool:
                if not state.get("hidden") and read_phase(phase_path) == clicked_phase:
                    _hide(until_phase_changes=clicked_phase)
                return False

            GLib.timeout_add(REQUEST_FALLBACK_HIDE_MS, _fallback)

        def _press(button: Any) -> None:
            state["press"] = (button, time.monotonic())
            area.queue_draw()

        def _answering() -> bool:
            return read_phase(phase_path) == "answer" or bool(state.get("answer_active"))

        def _button_at(ex: float, ey: float) -> str | None:
            lx = _to_strip_x(ex, ey)
            if lx < HIT_PAD:
                return "left"
            if lx > WIDTH - HIT_PAD:
                return "right"
            return None

        def handle_click(ex: float, ey: float) -> None:
            """A press released without moving: button / option / dismiss."""
            if state.get("hidden"):
                return
            armed_at = float(state.get("click_armed_at") or 0.0)
            if time.monotonic() < armed_at:
                return
            phase = read_phase(phase_path)
            if phase == "answer" or state.get("answer_active"):
                if state.get("anim_mode") == "collapse":
                    return
                if _answer_progress() < 0.85:
                    return
                hit = state.get("option_hit")
                opts = state.get("options") or []
                if hit and opts:
                    y0, line_h, count = hit
                    if y0 <= ey <= y0 + line_h * count:
                        idx = int((ey - y0) // max(line_h, 1.0))
                        if 0 <= idx < len(opts):
                            try:
                                write_command(control_path, f"option_{idx}")
                            except Exception as exc:
                                print(
                                    f"[vaani] indicator: option_{idx} failed: {exc!r}",
                                    flush=True,
                                )
                            # Flash the picked row, then dismiss.
                            _press(("option", idx))
                            GLib.timeout_add(
                                PRESS_ANIM_MS,
                                lambda: (_dismiss_answer_ui(), False)[1],
                            )
                            return
                # No option hit — dismiss card.
                _dismiss_answer_ui()
                return
            button = _button_at(ex, ey)
            if button == "left":
                _press("left")
                _request("cancel")
            elif button == "right":
                _press("right")
                # Recording: stop. Processing: cancel wait. Same visible control.
                _request("cancel" if phase == "processing" else "stop")

        # One gesture for click + drag: a press is a click unless the pointer
        # travels past DRAG_THRESHOLD, so the pill can be grabbed anywhere —
        # including on its buttons — without firing them.
        drag = Gtk.GestureDrag()
        drag.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)

        def drag_begin(_gesture, start_x: float, start_y: float) -> None:
            if state.get("hidden"):
                return
            ex, ey = start_x - P, start_y - P
            ptr = x11.pointer()
            _dbg(f"drag_begin start={start_x:.0f},{start_y:.0f} ptr={ptr} pos={state['x']},{state['y']}")
            x0, y0 = int(state["x"]), int(state["y"])
            state["drag"] = {
                "start": (ex, ey),
                "moved": False,
                "offset": (0.0, 0.0),
                # Where on the pill it was grabbed; root pointer − grab = position.
                "grab": (ptr[0] - x0, ptr[1] - y0) if ptr else (ex, ey),
                "pending": False,
            }
            if not _answering():
                state["held"] = _button_at(ex, ey)
            area.queue_draw()

        def apply_drag() -> bool:
            d = state.get("drag")
            if not isinstance(d, dict):
                return False
            d["pending"] = False
            ptr = x11.pointer()
            gx, gy = d["grab"]
            if ptr is not None:
                nx, ny = ptr[0] - gx, ptr[1] - gy
            else:
                # No root pointer: GTK's offset is widget-relative, and the
                # widget moves with us, so it is the delta from where we are.
                ox, oy = d["offset"]
                nx, ny = int(state["x"]) + ox, int(state["y"]) + oy
                ptr = (int(nx + gx), int(ny + gy))
            if state.get("answer_active"):
                old_x, old_y = int(state["x"]), int(state["y"])
                place(int(nx), int(ny))
                anchor = state.get("record_anchor")
                if isinstance(anchor, tuple) and len(anchor) == 2:
                    dx, dy = int(state["x"]) - old_x, int(state["y"]) - old_y
                    state["record_anchor"] = (int(anchor[0]) + dx, int(anchor[1]) + dy)
                return False
            w, h = size()
            _name, rect = monitor_at(ptr[0], ptr[1])
            want = _zone_orientation(ptr[1] - gy + h / 2.0, rect, str(state["orient"]))
            if want != state["orient"]:
                # Keep the same spot along the long axis under the pointer.
                along = (gy / h) if state["orient"] == "v" else (gx / w)
                set_orientation(want, pivot=(float(ptr[0]), float(ptr[1])))
                nw, nh = size()
                d["grab"] = (nw / 2.0, along * nh) if want == "v" else (along * nw, nh / 2.0)
                gx, gy = d["grab"]
                nx, ny = ptr[0] - gx, ptr[1] - gy
            place(int(round(nx)), int(round(ny)))
            return False

        def drag_update(_gesture, offset_x: float, offset_y: float) -> None:
            d = state.get("drag")
            if not isinstance(d, dict):
                return
            if not d["moved"]:
                if math.hypot(offset_x, offset_y) < DRAG_THRESHOLD:
                    return
                d["moved"] = True
                state["held"] = None
                state["hover_btn"] = None
            d["offset"] = (offset_x, offset_y)
            # Coalesce: at most one X move per main-loop pass.
            if not d["pending"]:
                d["pending"] = True
                GLib.idle_add(apply_drag, priority=GLib.PRIORITY_HIGH_IDLE)

        def drag_end(_gesture, *_args) -> None:
            d = state.get("drag")
            state["drag"] = None
            state["held"] = None
            _dbg(f"drag_end moved={isinstance(d, dict) and d['moved']}")
            if not isinstance(d, dict):
                return
            if d["moved"]:
                state["drag"] = d
                apply_drag()
                state["drag"] = None
                try:
                    persist_position()
                except Exception:
                    pass
            else:
                handle_click(*d["start"])
            area.queue_draw()

        drag.connect("drag-begin", drag_begin)
        drag.connect("drag-update", drag_update)
        drag.connect("drag-end", drag_end)
        area.add_controller(drag)

        motion = Gtk.EventControllerMotion()

        def on_enter(*_args) -> None:
            state["hover"] = True
            if state["answer_active"] and state.get("anim_mode") != "collapse":
                _cancel_dismiss_timer()

        def on_leave(*_args) -> None:
            state["hover"] = False
            state["hover_btn"] = None
            if state["answer_active"] and state.get("anim_mode") != "collapse":
                leave_ms = (
                    ANSWER_CLARIFY_DISMISS_MS
                    if state.get("options")
                    else ANSWER_LEAVE_DISMISS_MS
                )
                _schedule_dismiss(leave_ms)

        def on_motion(_ctl, x: float, y: float) -> None:
            if state.get("answer_active") or state.get("hidden") or state.get("drag"):
                state["hover_btn"] = None
                return
            state["hover_btn"] = _button_at(x - P, y - P)

        motion.connect("enter", on_enter)
        motion.connect("leave", on_leave)
        motion.connect("motion", on_motion)
        area.add_controller(motion)

        def on_close(*_args):
            _cancel_dismiss_timer()
            try:
                persist_position()
            except Exception:
                pass
            return False

        daemon_pid = int(os.environ.get("VAANI_DAEMON_PID") or 0)

        def tick() -> bool:
            phase = read_phase(phase_path)
            # Persistent pill must not outlive its daemon.
            state["ticks"] = int(state.get("ticks") or 0) + 1
            if daemon_pid > 1 and state["ticks"] % 60 == 0:
                try:
                    os.kill(daemon_pid, 0)
                except ProcessLookupError:
                    application.quit()
                    return False
                except OSError:
                    pass
            if state["ticks"] % 15 == 0 and not state.get("drag"):
                # Display hotplug / resolution change.
                check_monitors()
                guard_position()
            if phase == "idle":
                press = state.get("press")
                if (
                    not state.get("hidden")
                    and press
                    and time.monotonic() - float(press[1]) < PRESS_ANIM_MS / 1000.0
                ):
                    # Let the click animation finish before hiding.
                    area.queue_draw()
                    return True
                if not state.get("hidden"):
                    _hide()
                state["hidden_phase"] = None
                state["last_phase"] = phase
                return True
            if state.get("hidden"):
                if phase == state.get("hidden_phase"):
                    return True
                _show()
            prev = state.get("last_phase")
            if phase != prev or state["ticks"] % 15 == 0:
                state["session"] = read_session(session_file)
            if phase != prev:
                if prev in (None, "idle") and phase == "recording":
                    state["shown_at"] = time.monotonic()
                    # New session from idle: never carry an old card over.
                    if state.get("answer_active") or state.get("anim_mode") != "expand":
                        _reset_to_pill()
                state["last_phase"] = phase
                if phase in {"recording", "processing"}:
                    state["click_armed_at"] = time.monotonic() + CLICK_GRACE_S
            if phase == "answer":
                _enter_answer_phase()
                _refresh_answer_payload()
                if state.get("answer_active") and float(state.get("anim_progress") or 0) < 1.0:
                    _apply_answer_geometry(_answer_progress())
            elif phase == "recording":
                if state.get("answer_active") or state.get("anim_mode") == "collapse":
                    if state.get("anim_mode") != "collapse":
                        _begin_collapse_to_recording()
                    progress = _answer_progress()
                    _apply_answer_geometry(progress)
                    if progress >= 1.0:
                        _finish_collapse_to_recording()
                try:
                    wave.push(float(amplitude_path.read_text(encoding="utf-8").strip()))
                except Exception:
                    wave.push(0.0)
            elif phase == "processing" and state.get("answer_active"):
                # Processing after a prior answer shouldn't keep the Q&A card.
                if state.get("anim_mode") != "collapse":
                    _begin_collapse_to_recording()
                progress = _answer_progress()
                _apply_answer_geometry(progress)
                if progress >= 1.0:
                    _finish_collapse_to_recording()
            area.queue_draw()
            return True

        def on_realize(_widget):
            place()
            return False

        win.connect("realize", on_realize)
        win.connect("close-request", on_close)
        GLib.timeout_add(33, tick)
        state["click_armed_at"] = time.monotonic() + CLICK_GRACE_S
        set_content_size(*pill_size())
        # Starts hidden (offscreen, click-through) when prewarmed with phase=idle.
        win.present()
        # Re-apply after map; Mutter/GTK can ignore the first configure.
        for delay in (0, 50, 120, 250):
            GLib.timeout_add(delay, lambda d=delay: (place(), False)[1])
        print(
            f"[vaani] indicator ready target={state['x']},{state['y']} "
            f"orient={state['orient']} monitors={list(zip(names0, rects0))} x11={x11._mode}",
            flush=True,
        )

    app.connect("activate", activate)
    return int(app.run([]))

def _venv_site_packages() -> Path | None:
    """Resolve the active venv site-packages (uv-safe: use sys.prefix, not resolve(exe))."""
    prefix = Path(sys.prefix)
    for path in sorted(prefix.glob("lib/python*/site-packages")):
        if path.is_dir():
            return path
    env = os.environ.get("VIRTUAL_ENV")
    if env:
        for path in sorted(Path(env).glob("lib/python*/site-packages")):
            if path.is_dir():
                return path
    return None


def _reexec_with_system_python_if_needed() -> None:
    try:
        import gi  # noqa: F401

        gi.require_version("Gtk", "4.0")
        return
    except Exception:
        pass
    system = Path("/usr/bin/python3")
    if not system.exists() or Path(sys.executable).resolve() == system.resolve():
        return
    src = Path(__file__).resolve().parents[3]
    env = os.environ.copy()
    parts = [str(src)]
    site = _venv_site_packages()
    if site is not None:
        parts.append(str(site))
    prior = env.get("PYTHONPATH", "")
    if prior:
        parts.append(prior)
    env["PYTHONPATH"] = os.pathsep.join(parts)
    print(f"[vaani] indicator reexec system python PYTHONPATH={env['PYTHONPATH']}", flush=True)
    os.execve(
        str(system),
        [str(system), "-m", "vaani.platform.linux.indicator_app", *sys.argv[1:]],
        env,
    )


def main() -> int:
    from ...indicator_protocol import resolve_answer_path, resolve_control_path, resolve_phase_path

    _reexec_with_system_python_if_needed()

    amp = Path(os.environ.get("VAANI_AMPLITUDE_PATH", "/tmp/vaani-amplitude"))
    control = resolve_control_path()
    phase = resolve_phase_path()
    answer = resolve_answer_path()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    config = Path(xdg) if xdg else Path.home() / ".config"
    pos = config / "vaani" / "indicator.json"

    try:
        return run_gtk(
            amplitude_path=amp,
            control_path=control,
            position_path=pos,
            phase_path=phase,
            answer_path=answer,
        )
    except Exception as exc:
        print(f"[vaani] gtk indicator failed ({exc!r}); tk fallback", flush=True)
        from ...indicator_tk import run_pill

        return run_pill(
            amplitude_path=amp,
            control_path=control,
            position_path=pos,
            phase_path=phase,
        )


if __name__ == "__main__":
    raise SystemExit(main())
