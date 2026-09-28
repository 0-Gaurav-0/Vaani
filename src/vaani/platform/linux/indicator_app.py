"""Linux floating recording indicator — transparent GTK4 overlay.

No bar / capsule fill: only cancel, waveform bars, and confirm are drawn.
Positions via X11 (python-xlib or ctypes libX11) as an override-redirect window
so Mutter cannot leave it stuck at (0,0). Defaults to center-bottom.
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
ANSWER_MIN_WIDTH = 200
ANSWER_MAX_WIDTH = 480
ANSWER_TEXT_MIN_HEIGHT = 44
MARGIN_BOTTOM = 48
BAR_COUNT = 22
HIT_PAD = 36
# Ignore clicks right after the pill maps — Mutter/GTK sometimes delivers a
# ghost press on the left cancel pad ~1s after spawn (kills snap sessions).
CLICK_GRACE_S = 0.75
ANSWER_AUTO_DISMISS_MS = 9000
ANSWER_CLARIFY_DISMISS_MS = 45000
ANSWER_LEAVE_DISMISS_MS = 5000
ANSWER_TEXT_LEFT = 16.0
ANSWER_PAD_RIGHT = 16.0
ANSWER_Q_SIZE = 13.0
ANSWER_A_SIZE = 15.0
ANSWER_Q_LINE = 17.0
ANSWER_A_LINE = 19.0
ANSWER_GAP = 14.0
ANSWER_TOP = 16.0
ANSWER_TEXT_BOTTOM_PAD = 16.0
ANSWER_RADIUS = 16.0
ANSWER_ANIM_MS = 280
# ---- look (Wispr-Flow-like capsule) ----
FONT = "Ubuntu"
MONO_FONT = "Ubuntu Mono"
CAPSULE_FILL = (0.047, 0.047, 0.055, 0.94)
CAPSULE_BORDER = (1.0, 1.0, 1.0, 0.11)
DICTATION_ACCENT = (0.96, 0.96, 0.97)
ASSISTANT_ACCENT = (0.66, 0.55, 0.98)  # violet
WARN_ACCENT = (0.98, 0.75, 0.14)  # amber, last minute
DANGER_ACCENT = (0.97, 0.33, 0.33)  # red, last 15s
APPEAR_MS = 170
TIMER_AFTER_S = 20.0

# Button press feedback: quick squeeze + halo ring.
PRESS_ANIM_MS = 220
# Hidden (idle) pill parks here: mapped but invisible and click-through.
OFFSCREEN = (-4000, -4000)
# If the daemon never answers a pill click, hide locally after this.
REQUEST_FALLBACK_HIDE_MS = 1500


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


def _save_position(path: Path, x: int, y: int) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps({"x": int(x), "y": int(y)}), encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        pass


def _screen_geometry(display) -> tuple[int, int, int, int]:
    try:
        monitors = display.get_monitors()
        monitor = monitors.get_item(0) if monitors.get_n_items() else None
        if monitor is not None:
            geo = monitor.get_geometry()
            return int(geo.x), int(geo.y), int(geo.width), int(geo.height)
    except Exception:
        pass
    return 0, 0, 1920, 1080


def _default_pos(
    ox: int, oy: int, sw: int, sh: int, *, w: int = WIDTH, h: int = HEIGHT
) -> tuple[int, int]:
    return ox + max(0, (sw - w) // 2), oy + max(0, sh - h - MARGIN_BOTTOM)


def _clamp_pos(
    x: int, y: int, ox: int, oy: int, sw: int, sh: int, *, w: int = WIDTH, h: int = HEIGHT
) -> tuple[int, int]:
    return (
        max(ox, min(int(x), ox + max(0, sw - w))),
        max(oy, min(int(y), oy + max(0, sh - h))),
    )



def _answer_anchor_pos(
    old_x: int,
    old_y: int,
    old_w: int,
    old_h: int,
    new_w: int,
    new_h: int,
    display,
) -> tuple[int, int]:
    """Place answer card: grow up, stay horizontally centered on screen.

    Avoids the leftward drift users see when only the pill's left edge is kept
    while width grows.
    """
    ox, oy, sw, sh = _screen_geometry(display)
    bottom = old_y + old_h
    nx = ox + max(0, (sw - new_w) // 2)
    ny = bottom - new_h
    return _clamp_pos(nx, ny, ox, oy, sw, sh, w=new_w, h=new_h)


def _wrap_cairo_text(cr, text: str, max_width: float) -> list[str]:
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
    """Minimal X11 helpers — python-xlib preferred, ctypes fallback."""

    def __init__(self) -> None:
        self._mode = "none"
        self._xlib = None
        self._lib = None
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
                lib.XGetGeometry.argtypes = [
                    ctypes.c_void_p,
                    ctypes.c_ulong,
                    ctypes.POINTER(ctypes.c_ulong),
                    ctypes.POINTER(ctypes.c_int),
                    ctypes.POINTER(ctypes.c_int),
                    ctypes.POINTER(ctypes.c_uint),
                    ctypes.POINTER(ctypes.c_uint),
                    ctypes.POINTER(ctypes.c_uint),
                    ctypes.POINTER(ctypes.c_uint),
                ]
                lib.XGetGeometry.restype = ctypes.c_int
                lib.XQueryTree.argtypes = [
                    ctypes.c_void_p,
                    ctypes.c_ulong,
                    ctypes.POINTER(ctypes.c_ulong),
                    ctypes.POINTER(ctypes.c_ulong),
                    ctypes.POINTER(ctypes.POINTER(ctypes.c_ulong)),
                    ctypes.POINTER(ctypes.c_uint),
                ]
                lib.XQueryTree.restype = ctypes.c_int
                lib.XFree.argtypes = [ctypes.c_void_p]
                # CWOverrideRedirect = 1<<9
                class XSetWindowAttributes(ctypes.Structure):
                    _fields_ = [("pad", ctypes.c_byte * 128)]  # oversized; set via raw

                self._lib = lib
                self._mode = "ctypes"
            except Exception:
                self._mode = "none"

    @property
    def ok(self) -> bool:
        return self._mode != "none"

    def prepare(self, xid: int) -> bool:
        if self._mode == "xlib":
            display, X = self._xlib
            dpy = display.Display()
            try:
                win = self._toplevel_xlib(dpy, xid)
                win.change_attributes(override_redirect=1)
                dpy.sync()
                return True
            finally:
                dpy.close()
        if self._mode == "ctypes":
            # Best-effort: move/raise still work without override-redirect.
            return True
        return False

    def move(self, xid: int, x: int, y: int) -> bool:
        if self._mode == "xlib":
            display, X = self._xlib
            dpy = display.Display()
            try:
                win = self._toplevel_xlib(dpy, xid)
                win.change_attributes(override_redirect=1)
                win.configure(x=int(x), y=int(y), stack_mode=X.Above)
                try:
                    win.map()
                except Exception:
                    pass
                dpy.sync()
                return True
            finally:
                dpy.close()
        if self._mode == "ctypes":
            lib = self._lib
            dpy = lib.XOpenDisplay(None)
            if not dpy:
                return False
            try:
                top = self._toplevel_ctypes(lib, dpy, int(xid))
                lib.XMoveWindow(dpy, top, int(x), int(y))
                lib.XRaiseWindow(dpy, top)
                lib.XMapRaised(dpy, top)
                lib.XFlush(dpy)
                return True
            finally:
                lib.XCloseDisplay(dpy)
        return False

    def get_pos(self, xid: int) -> tuple[int, int] | None:
        if self._mode == "xlib":
            display, _X = self._xlib
            dpy = display.Display()
            try:
                win = self._toplevel_xlib(dpy, xid)
                root = dpy.screen().root
                # python-xlib returns a TranslateCoords reply object, not a tuple.
                coords = root.translate_coords(win, 0, 0)
                return int(coords.x), int(coords.y)
            except Exception:
                return None
            finally:
                dpy.close()
        if self._mode == "ctypes":
            lib = self._lib
            dpy = lib.XOpenDisplay(None)
            if not dpy:
                return None
            try:
                top = self._toplevel_ctypes(lib, dpy, int(xid))
                root = ctypes.c_ulong()
                x = ctypes.c_int()
                y = ctypes.c_int()
                w = ctypes.c_uint()
                h = ctypes.c_uint()
                bw = ctypes.c_uint()
                depth = ctypes.c_uint()
                lib.XGetGeometry(
                    dpy, top, ctypes.byref(root), ctypes.byref(x), ctypes.byref(y),
                    ctypes.byref(w), ctypes.byref(h), ctypes.byref(bw), ctypes.byref(depth),
                )
                # Geometry is parent-relative; walk to root by summing if needed.
                # For override-redirect / toplevel under root, x/y are root-relative.
                return int(x.value), int(y.value)
            finally:
                lib.XCloseDisplay(dpy)
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
    t0 = time.monotonic()
    answer_file = answer_path if answer_path is not None else resolve_answer_path()
    session_file = resolve_session_path()
    app = Gtk.Application(application_id="com.vaani.RecordingIndicator")

    def activate(application: Gtk.Application) -> None:
        win = Gtk.Window(application=application)
        win.set_title("Vaani")
        win.set_decorated(False)
        win.set_resizable(False)
        win.set_default_size(WIDTH, HEIGHT)

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
        area.set_content_width(WIDTH)
        area.set_content_height(HEIGHT)
        win.set_child(area)

        ox, oy, sw, sh = _screen_geometry(display)
        saved = _load_position(position_path)
        if saved is not None:
            px, py = _clamp_pos(saved[0], saved[1], ox, oy, sw, sh)
        else:
            px, py = _default_pos(ox, oy, sw, sh)

        state: dict[str, object] = {
            "x": px,
            "y": py,
            "w": WIDTH,
            "h": HEIGHT,
            "dragging": False,
            "drag_origin": None,
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
                        else cairo.Region(cairo.RectangleInt(0, 0, w, h))
                    )
                    surface.set_input_region(region)
            except Exception:
                pass

        def place(x: int | None = None, y: int | None = None) -> bool:
            if state.get("hidden"):
                surface = win.get_surface()
                if surface is None:
                    return False
                apply_chrome(surface)
                xid = _surface_xid(surface)
                if xid is None:
                    return False
                x11.prepare(xid)
                return x11.move(xid, OFFSCREEN[0], OFFSCREEN[1])
            ox_, oy_, sw_, sh_ = _screen_geometry(display)
            w, h = size()
            px_ = int(state["x"] if x is None else x)
            py_ = int(state["y"] if y is None else y)
            px_, py_ = _clamp_pos(px_, py_, ox_, oy_, sw_, sh_, w=w, h=h)
            state["x"], state["y"] = px_, py_
            surface = win.get_surface()
            if surface is None:
                return False
            apply_chrome(surface)
            xid = _surface_xid(surface)
            if xid is None:
                print("[vaani] indicator: no xid", flush=True)
                return False
            x11.prepare(xid)
            ok = x11.move(xid, px_, py_)
            if ok:
                state["moves_ok"] = int(state["moves_ok"]) + 1
            else:
                print(f"[vaani] indicator: move failed target={px_},{py_}", flush=True)
            return ok

        def persist_position() -> None:
            # Never persist the expanded answer-card geometry — that shifts the
            # next recording pill left/right of bottom-center.
            if state.get("answer_active"):
                anchor = state.get("record_anchor")
                if isinstance(anchor, tuple) and len(anchor) == 2:
                    _save_position(position_path, int(anchor[0]), int(anchor[1]))
                return
            surface = win.get_surface()
            xid = _surface_xid(surface) if surface is not None else None
            pos = x11.get_pos(xid) if xid is not None else None
            ox_, oy_, sw_, sh_ = _screen_geometry(display)
            w, h = size()
            if pos is not None:
                px_, py_ = _clamp_pos(pos[0], pos[1], ox_, oy_, sw_, sh_, w=w, h=h)
            else:
                px_, py_ = _clamp_pos(
                    int(state["x"]), int(state["y"]), ox_, oy_, sw_, sh_, w=w, h=h
                )
            # Ignore bogus top-left saves if we never successfully moved.
            if px_ <= 2 and py_ <= 2 and int(state["moves_ok"]) == 0:
                px_, py_ = _default_pos(ox_, oy_, sw_, sh_, w=w, h=h)
            state["x"], state["y"] = px_, py_
            _save_position(position_path, px_, py_)

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
            state["w"], state["h"] = max(1, aw), max(1, ah)
            state["x"], state["y"] = ax, ay
            state["anim_progress"] = p
            win.set_default_size(aw, ah)
            area.set_content_width(aw)
            area.set_content_height(ah)
            try:
                win.set_size_request(aw, ah)
            except Exception:
                pass
            place(ax, ay)

        def _finish_collapse_to_recording() -> None:
            state["answer_active"] = False
            state["question"] = ""
            state["answer"] = ""
            state["anim_mode"] = "expand"
            state["anim_progress"] = 1.0
            state["w"], state["h"] = WIDTH, HEIGHT
            win.set_default_size(WIDTH, HEIGHT)
            area.set_content_width(WIDTH)
            area.set_content_height(HEIGHT)
            try:
                win.set_size_request(WIDTH, HEIGHT)
            except Exception:
                pass
            place()

        def _begin_collapse_to_recording() -> None:
            """Reverse-morph answer card back into the recording pill."""
            if state.get("anim_mode") == "collapse":
                return
            _cancel_dismiss_timer()
            old_w, old_h = size()
            old_x, old_y = int(state["x"]), int(state["y"])
            ox, oy, sw, sh = _screen_geometry(display)
            anchor = state.get("record_anchor")
            if isinstance(anchor, tuple) and len(anchor) == 2:
                nx, ny = int(anchor[0]), int(anchor[1])
            else:
                nx, ny = _default_pos(ox, oy, sw, sh, w=WIDTH, h=HEIGHT)
            nx, ny = _clamp_pos(nx, ny, ox, oy, sw, sh, w=WIDTH, h=HEIGHT)
            state["anim_from"] = (old_w, old_h, old_x, old_y)
            state["anim_to"] = (WIDTH, HEIGHT, nx, ny)
            state["anim_t0"] = time.monotonic()
            state["anim_progress"] = 0.0
            state["anim_mode"] = "collapse"
            state["answer_active"] = True  # keep drawing text until fade completes
            _apply_answer_geometry(0.0)

        def _enter_answer_phase() -> None:
            if state["answer_active"] and state.get("anim_mode") != "collapse":
                return
            if state.get("anim_mode") == "collapse":
                return
            payload = read_answer(answer_file)
            state["question"] = str(payload.get("question") or "")
            state["answer"] = str(payload.get("answer") or "")
            opts = payload.get("options") if isinstance(payload.get("options"), list) else []
            state["options"] = [str(item) for item in opts[:5] if str(item).strip()]
            old_w, old_h = size()
            old_x, old_y = int(state["x"]), int(state["y"])
            aw, ah = _measure_answer_size(
                state["question"], state["answer"], cairo_mod=cairo
            )
            # Grow upward; keep the card on the screen's horizontal center.
            state["record_anchor"] = (old_x, old_y)
            nx, ny = _answer_anchor_pos(old_x, old_y, old_w, old_h, aw, ah, display)
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
            """Hover glow + press halo. Returns the squeeze scale for the icon."""
            k = _press_progress(button)
            if state.get("hover_btn") == button and k is None:
                cr.set_source_rgba(1, 1, 1, 0.10 * a)
                cr.arc(bx, cy, r + 3.0, 0, 2 * math.pi)
                cr.fill()
            if k is None:
                return 1.0
            cr.set_source_rgba(1, 1, 1, 0.45 * (1.0 - k) * a)
            cr.set_line_width(2.0)
            cr.arc(bx, cy, r + 2.0 + 8.0 * k, 0, 2 * math.pi)
            cr.stroke()
            return 1.0 - 0.2 * math.sin(math.pi * k)

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

        def _draw_capsule(cr, x: float, y: float, w: float, h: float, a: float, border=None) -> None:
            _rounded_rect(cr, x, y, w, h, h / 2.0)
            cr.set_source_rgba(*CAPSULE_FILL[:3], CAPSULE_FILL[3] * a)
            cr.fill_preserve()
            # Soft top sheen.
            grad = cairo.LinearGradient(0, y, 0, y + h)
            grad.add_color_stop_rgba(0.0, 1, 1, 1, 0.07 * a)
            grad.add_color_stop_rgba(0.5, 1, 1, 1, 0.0)
            cr.set_source(grad)
            cr.fill_preserve()
            bc = border or CAPSULE_BORDER
            cr.set_source_rgba(bc[0], bc[1], bc[2], (bc[3] if len(bc) > 3 else 0.6) * a)
            cr.set_line_width(1.0)
            cr.stroke()

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
            """Hover glow + press halo. Returns the squeeze scale for the icon."""
            k = _press_progress(button)
            if state.get("hover_btn") == button and k is None:
                cr.set_source_rgba(1, 1, 1, 0.08 * a)
                cr.arc(bx, cy, r + 3.0, 0, 2 * math.pi)
                cr.fill()
            if k is None:
                return 1.0
            cr.set_source_rgba(1, 1, 1, 0.40 * (1.0 - k) * a)
            cr.set_line_width(1.6)
            cr.arc(bx, cy, r + 1.5 + 7.0 * k, 0, 2 * math.pi)
            cr.stroke()
            return 1.0 - 0.18 * math.sin(math.pi * k)

        def _fmt(seconds: float) -> str:
            seconds = max(0, int(seconds))
            return f"{seconds // 60}:{seconds % 60:02d}"

        def _draw_control_strip(
            cr, width: int, height: int, *, phase: str, alpha: float = 1.0
        ) -> None:
            """Capsule: ✕ · waveform/shimmer (+ timer) · stop."""
            if alpha <= 0.01:
                return
            a = max(0.0, min(1.0, float(alpha)))
            now = time.monotonic()
            # Appear: spring the capsule open from the centre.
            k = min(1.0, (now - float(state.get("shown_at") or 0.0)) / (APPEAR_MS / 1000.0))
            grow = 1.0 - (1.0 - k) ** 3
            a *= 0.35 + 0.65 * grow
            full_w = width - 4.0
            cap_w = full_w * (0.72 + 0.28 * grow)
            cap_h = HEIGHT - 4.0
            cap_x = (width - cap_w) / 2.0
            cap_y = height - HEIGHT + 2.0
            cy = cap_y + cap_h / 2.0
            accent = _accent()
            warn = _warn_level() if phase == "recording" else 0
            border = None
            if warn:
                col = DANGER_ACCENT if warn == 2 else WARN_ACCENT
                pulse = 0.55 + 0.45 * abs(math.sin(now * (5.0 if warn == 2 else 2.4)))
                border = (col[0], col[1], col[2], pulse)
            _draw_capsule(cr, cap_x, cap_y, cap_w, cap_h, a, border)
            if grow < 0.6:
                return
            ca = a * min(1.0, (grow - 0.6) / 0.4)

            # Left: cancel.
            btn_r = 12.0
            lx = cap_x + 4.0 + btn_r
            sq = _draw_button_feedback(cr, lx, cy, btn_r, "left", ca)
            pressed = _press_progress("left") is not None
            cr.set_source_rgba(1, 1, 1, (0.18 if pressed else 0.09) * ca)
            cr.arc(lx, cy, btn_r * sq, 0, 2 * math.pi)
            cr.fill()
            cr.set_source_rgba(0.92, 0.92, 0.94, 0.9 * ca)
            cr.set_line_width(1.6)
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            d = 3.6 * sq
            cr.move_to(lx - d, cy - d)
            cr.line_to(lx + d, cy + d)
            cr.move_to(lx + d, cy - d)
            cr.line_to(lx - d, cy + d)
            cr.stroke()

            # Right: stop (recording) / cancel-able spinner (processing).
            rx = cap_x + cap_w - 4.0 - btn_r
            sq = _draw_button_feedback(cr, rx, cy, btn_r, "right", ca)
            pressed = _press_progress("right") is not None
            if phase == "processing":
                cr.set_source_rgba(1, 1, 1, 0.09 * ca)
                cr.arc(rx, cy, btn_r * sq, 0, 2 * math.pi)
                cr.fill()
                start = (now * 5.0) % (2 * math.pi)
                cr.set_source_rgba(accent[0], accent[1], accent[2], 0.95 * ca)
                cr.set_line_width(2.0)
                cr.arc(rx, cy, 6.0 * sq, start, start + math.pi * 1.25)
                cr.stroke()
            else:
                fill = 0.78 if pressed else 1.0
                btn_col = accent
                if warn:
                    btn_col = DANGER_ACCENT if warn == 2 else WARN_ACCENT
                cr.set_source_rgba(btn_col[0] * fill, btn_col[1] * fill, btn_col[2] * fill, 0.98 * ca)
                cr.arc(rx, cy, btn_r * sq, 0, 2 * math.pi)
                cr.fill()
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
            label_col = (0.72, 0.72, 0.76)
            if phase == "recording" and elapsed > 0:
                if warn:
                    remaining = float(sess.get("max_s") or 0.0) - elapsed
                    label = f"{_fmt(remaining)} left"
                    label_col = DANGER_ACCENT if warn == 2 else WARN_ACCENT
                elif sess.get("handsfree") or elapsed >= TIMER_AFTER_S:
                    label = _fmt(elapsed)
            if label:
                cr.select_font_face(MONO_FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD if warn else cairo.FONT_WEIGHT_NORMAL)
                cr.set_font_size(11.5)
                ext = cr.text_extents(label)
                tx = inner_right - ext.x_advance
                cr.set_source_rgba(label_col[0], label_col[1], label_col[2], 0.95 * ca)
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
                bar_h = max(2.6, (max(0.0, min(1.0, float(level))) ** 1.35) * max_h)
                cr.set_source_rgba(accent[0], accent[1], accent[2], 0.95 * alpha_i)
                _rounded_rect(cr, bx - bar_w / 2, cy - bar_h / 2, bar_w, bar_h, bar_w / 2)
                cr.fill()

        def draw(_area, cr, width: int, height: int) -> None:
            cr.set_operator(cairo.OPERATOR_SOURCE)
            cr.set_source_rgba(0, 0, 0, 0)
            cr.paint()
            cr.set_operator(cairo.OPERATOR_OVER)

            if state.get("hidden"):
                return
            phase = read_phase(phase_path)
            if phase == "answer" or state.get("answer_active"):
                progress = _answer_progress()
                collapsing = state.get("anim_mode") == "collapse"
                if collapsing:
                    chrome_a = progress
                    text_a = max(0.0, 1.0 - progress)
                    fill_a = 0.92 * (1.0 - 0.75 * progress)
                    radius = ANSWER_RADIUS + (12.0 - ANSWER_RADIUS) * progress
                else:
                    chrome_a = max(0.0, 1.0 - progress)
                    text_a = progress
                    fill_a = 0.92 * (0.25 + 0.75 * progress)
                    radius = 12.0 + (ANSWER_RADIUS - 12.0) * progress

                _rounded_rect(cr, 1.0, 1.0, width - 2.0, height - 2.0, radius)
                cr.set_source_rgba(*CAPSULE_FILL[:3], fill_a)
                cr.fill_preserve()
                cr.set_source_rgba(*CAPSULE_BORDER[:3], CAPSULE_BORDER[3] * (fill_a / 0.92))
                cr.set_line_width(1.0)
                cr.stroke()

                if text_a > 0.02:
                    text_left = ANSWER_TEXT_LEFT
                    text_width = max(40.0, width - text_left - ANSWER_PAD_RIGHT)
                    cr.select_font_face(
                        FONT, cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL
                    )
                    cr.set_font_size(ANSWER_Q_SIZE)
                    cr.set_source_rgba(0.72, 0.74, 0.78, 0.95 * text_a)
                    q_lines = _wrap_cairo_text(
                        cr, str(state.get("question") or ""), text_width
                    )
                    y = ANSWER_TOP
                    for line in q_lines[:3]:
                        cr.move_to(text_left, y)
                        cr.show_text(line)
                        y += ANSWER_Q_LINE

                    sep_y = y + ANSWER_GAP * 0.35
                    cr.set_source_rgba(1, 1, 1, 0.10 * text_a)
                    cr.set_line_width(1.0)
                    cr.move_to(text_left, sep_y)
                    cr.line_to(width - ANSWER_PAD_RIGHT, sep_y)
                    cr.stroke()

                    cr.set_font_size(ANSWER_A_SIZE)
                    cr.set_source_rgba(0.94, 0.95, 0.97, 0.98 * text_a)
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
                            cr.rectangle(
                                text_left - 6, ry, text_width + 12, ANSWER_A_LINE
                            )
                            cr.fill()
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

                if chrome_a > 0.02:
                    # Expand: chrome fades out. Collapse: chrome fades back in.
                    _draw_control_strip(
                        cr, width, height, phase="recording", alpha=chrome_a
                    )
                return

            # Recording / processing: chrome only (no capsule fill).
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

        def on_click_pressed(_gesture, _n, x: float, y: float) -> None:
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
                    if y0 <= y <= y0 + line_h * count:
                        idx = int((y - y0) // max(line_h, 1.0))
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
            if x < HIT_PAD:
                _press("left")
                _request("cancel")
                return
            if x > size()[0] - HIT_PAD:
                _press("right")
                # Recording: stop. Processing: cancel wait. Same visible control.
                if phase == "processing":
                    _request("cancel")
                else:
                    _request("stop")

        click = Gtk.GestureClick()
        click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        click.connect("pressed", on_click_pressed)
        area.add_controller(click)

        drag = Gtk.GestureDrag()
        drag.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)

        def drag_begin(_gesture, start_x: float, start_y: float) -> None:
            w, _h = size()
            phase = read_phase(phase_path)
            answering = phase == "answer" or bool(state.get("answer_active"))
            if answering:
                state["dragging"] = True
                state["drag_origin"] = (int(state["x"]), int(state["y"]))
                return
            if start_x < HIT_PAD or start_x > w - HIT_PAD:
                state["dragging"] = False
                return
            state["dragging"] = True
            state["drag_origin"] = (int(state["x"]), int(state["y"]))

        def drag_update(_gesture, offset_x: float, offset_y: float) -> None:
            if not state["dragging"]:
                return
            origin = state["drag_origin"]
            if not isinstance(origin, tuple):
                return
            place(origin[0] + int(offset_x), origin[1] + int(offset_y))

        def drag_end(_gesture, *_args) -> None:
            if state["dragging"]:
                try:
                    persist_position()
                except Exception:
                    pass
            state["dragging"] = False
            state["drag_origin"] = None

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

        def on_motion(_ctl, x: float, _y: float) -> None:
            if state.get("answer_active") or state.get("hidden"):
                state["hover_btn"] = None
                return
            if x < HIT_PAD:
                state["hover_btn"] = "left"
            elif x > size()[0] - HIT_PAD:
                state["hover_btn"] = "right"
            else:
                state["hover_btn"] = None

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
                state["last_phase"] = phase
                if phase in {"recording", "processing"}:
                    state["click_armed_at"] = time.monotonic() + CLICK_GRACE_S
            if phase == "answer":
                _enter_answer_phase()
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
        # Starts hidden (offscreen, click-through) when prewarmed with phase=idle.
        win.present()
        # Re-apply after map; Mutter/GTK can ignore the first configure.
        for delay in (0, 50, 120, 250):
            GLib.timeout_add(delay, lambda d=delay: (place(), False)[1])
        print(
            f"[vaani] indicator ready target={state['x']},{state['y']} x11={x11._mode}",
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
