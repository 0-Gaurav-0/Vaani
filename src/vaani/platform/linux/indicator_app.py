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

WIDTH = 176
HEIGHT = 40
MARGIN_BOTTOM = 48
BAR_COUNT = 15
HIT_PAD = 34


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


def _default_pos(ox: int, oy: int, sw: int, sh: int) -> tuple[int, int]:
    return ox + max(0, (sw - WIDTH) // 2), oy + max(0, sh - HEIGHT - MARGIN_BOTTOM)


def _clamp_pos(x: int, y: int, ox: int, oy: int, sw: int, sh: int) -> tuple[int, int]:
    return (
        max(ox, min(int(x), ox + max(0, sw - WIDTH))),
        max(oy, min(int(y), oy + max(0, sh - HEIGHT))),
    )


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
) -> int:
    import cairo
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    from gi.repository import Gdk, GLib, Gtk

    from ...indicator_protocol import read_phase, write_command
    from ...waveform import WaveformBuffer

    x11 = _x11()
    wave = WaveformBuffer(bars=BAR_COUNT)
    t0 = time.monotonic()
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
            "dragging": False,
            "drag_origin": None,
            "moves_ok": 0,
        }

        def apply_chrome(surface) -> None:
            try:
                if hasattr(surface, "set_opaque_region"):
                    surface.set_opaque_region(None)
            except Exception:
                pass
            try:
                if hasattr(surface, "set_input_region"):
                    surface.set_input_region(
                        cairo.Region(cairo.RectangleInt(0, 0, WIDTH, HEIGHT))
                    )
            except Exception:
                pass

        def place(x: int | None = None, y: int | None = None) -> bool:
            ox_, oy_, sw_, sh_ = _screen_geometry(display)
            px_ = int(state["x"] if x is None else x)
            py_ = int(state["y"] if y is None else y)
            px_, py_ = _clamp_pos(px_, py_, ox_, oy_, sw_, sh_)
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
            surface = win.get_surface()
            xid = _surface_xid(surface) if surface is not None else None
            pos = x11.get_pos(xid) if xid is not None else None
            ox_, oy_, sw_, sh_ = _screen_geometry(display)
            if pos is not None:
                px_, py_ = _clamp_pos(pos[0], pos[1], ox_, oy_, sw_, sh_)
            else:
                px_, py_ = _clamp_pos(int(state["x"]), int(state["y"]), ox_, oy_, sw_, sh_)
            # Ignore bogus top-left saves if we never successfully moved.
            if px_ <= 2 and py_ <= 2 and int(state["moves_ok"]) == 0:
                px_, py_ = _default_pos(ox_, oy_, sw_, sh_)
            state["x"], state["y"] = px_, py_
            _save_position(position_path, px_, py_)

        def draw(_area, cr, width: int, height: int) -> None:
            cr.set_operator(cairo.OPERATOR_SOURCE)
            cr.set_source_rgba(0, 0, 0, 0)
            cr.paint()
            cr.set_operator(cairo.OPERATOR_OVER)

            cy = height / 2.0
            cx, r = 18.0, 12.0
            cr.set_source_rgba(0.18, 0.19, 0.22, 0.88)
            cr.arc(cx, cy, r, 0, 2 * math.pi)
            cr.fill()
            cr.set_source_rgba(0.95, 0.96, 0.98, 0.95)
            cr.set_line_width(1.7)
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            cr.move_to(cx - 4, cy - 4)
            cr.line_to(cx + 4, cy + 4)
            cr.move_to(cx + 4, cy - 4)
            cr.line_to(cx - 4, cy + 4)
            cr.stroke()

            phase = read_phase(phase_path)
            if phase == "processing":
                t = time.monotonic() - t0
                for i in range(3):
                    pulse = 0.35 + 0.65 * abs(math.sin(t * 2.6 + i * 0.9))
                    cr.set_source_rgba(0.9, 0.92, 0.95, 0.35 + 0.55 * pulse)
                    dx = width / 2 - 16 + i * 16
                    pr = 2.4 + 1.1 * pulse
                    cr.arc(dx, cy, pr, 0, 2 * math.pi)
                    cr.fill()
                return

            kx, kr = width - 18.0, 12.0
            cr.set_source_rgba(0.95, 0.96, 0.98, 0.95)
            cr.arc(kx, cy, kr, 0, 2 * math.pi)
            cr.fill()
            cr.set_source_rgba(0.14, 0.15, 0.18, 0.95)
            cr.set_line_width(1.8)
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            cr.move_to(kx - 4.2, cy)
            cr.line_to(kx - 1.0, cy + 3.4)
            cr.line_to(kx + 4.6, cy - 3.4)
            cr.stroke()

            samples = wave.bars_now()
            inner_left, inner_right = 40.0, width - 40.0
            span = inner_right - inner_left
            max_h = height - 14.0
            for i, level in enumerate(samples):
                bx = inner_left + (i + 0.5) * span / BAR_COUNT
                bar_h = max(2.5, float(level) * max_h)
                half = 1.45
                y0 = cy - bar_h / 2
                cr.set_source_rgba(0.92, 0.94, 0.97, 0.92)
                cr.new_sub_path()
                cr.arc(bx, y0 + half, half, math.pi, 0)
                cr.arc(bx, y0 + bar_h - half, half, 0, math.pi)
                cr.close_path()
                cr.fill()

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
            win.close()

        def on_click_pressed(_gesture, _n, x: float, _y: float) -> None:
            phase = read_phase(phase_path)
            if x < HIT_PAD:
                _request("cancel")
                return
            if phase != "processing" and x > WIDTH - HIT_PAD:
                _request("stop")

        click = Gtk.GestureClick()
        click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        click.connect("pressed", on_click_pressed)
        area.add_controller(click)

        drag = Gtk.GestureDrag()
        drag.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)

        def drag_begin(_gesture, start_x: float, _start_y: float) -> None:
            if start_x < HIT_PAD or start_x > WIDTH - HIT_PAD:
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

        def on_close(*_args):
            persist_position()
            return False

        def tick() -> bool:
            phase = read_phase(phase_path)
            if phase == "recording":
                try:
                    wave.push(float(amplitude_path.read_text(encoding="utf-8").strip()))
                except Exception:
                    wave.push(0.0)
            area.queue_draw()
            return True

        def on_realize(_widget):
            place()
            return False

        win.connect("realize", on_realize)
        win.connect("close-request", on_close)
        GLib.timeout_add(33, tick)
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
    from ...indicator_protocol import resolve_control_path, resolve_phase_path

    _reexec_with_system_python_if_needed()

    amp = Path(os.environ.get("VAANI_AMPLITUDE_PATH", "/tmp/vaani-amplitude"))
    control = resolve_control_path()
    phase = resolve_phase_path()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    config = Path(xdg) if xdg else Path.home() / ".config"
    pos = config / "vaani" / "indicator.json"

    try:
        return run_gtk(
            amplitude_path=amp,
            control_path=control,
            position_path=pos,
            phase_path=phase,
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
