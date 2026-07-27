"""Shared tkinter floating recording pill (macOS + Windows, Linux fallback).

No capsule / grey bar: only cancel, waveform, and confirm are drawn. Where the
toolkit allows it, the window background is fully transparent so only those
controls are visible.
"""
from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

from .indicator_protocol import read_phase
from .waveform import WaveformBuffer

WIDTH = 176
HEIGHT = 40
MARGIN_BOTTOM = 48
BAR_COUNT = 15
HIT_PAD = 34

# Chroma key used for full transparency on platforms that support it.
KEY = "#ff00ff"
CANCEL_FILL = "#2e3036"
CANCEL_X = "#f0f2f5"
CONFIRM_FILL = "#f2f3f6"
CONFIRM_MARK = "#1f2126"
WAVE = "#e8ebf0"


def _load_position(path: Path) -> tuple[int, int] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if "y" in data:
            return int(data["x"]), int(data["y"])
        # Legacy: x-only files were bottom-pinned.
        return int(data["x"]), -1
    except Exception:
        return None


def _save_position(path: Path, x: int, y: int) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"x": int(x), "y": int(y)}), encoding="utf-8")
    except Exception:
        pass


def _send(control: Path, action: str) -> None:
    try:
        from .indicator_protocol import write_command

        write_command(control, action)
    except Exception:
        pass


def _default_y(screen_h: int) -> int:
    return max(0, screen_h - HEIGHT - MARGIN_BOTTOM)


def _circle_rects(cx: int, cy: int, r: int) -> list[tuple[int, int, int, int]]:
    rects: list[tuple[int, int, int, int]] = []
    for y in range(-r, r + 1):
        w = int(math.sqrt(max(0, r * r - y * y)))
        if w <= 0:
            continue
        rects.append((cx - w, cy + y, max(1, 2 * w), 1))
    return rects


def _apply_x11_shape(root, rects: list[tuple[int, int, int, int]]) -> None:
    """Make non-drawn areas pass-through so the window is not a solid rectangle."""
    if not rects:
        return
    try:
        from Xlib import display
        from Xlib.ext import shape
    except Exception:
        return
    try:
        dpy = display.Display()
        win = dpy.create_resource_object("window", int(root.winfo_id()))
        targets = [win]
        try:
            parent = win.query_tree().parent
            if parent is not None:
                targets.append(parent)
        except Exception:
            pass
        # Unsorted ordering — bar heights change every frame.
        ordering = getattr(getattr(shape, "Ordering", object), "Unsorted", 0)
        for target in targets:
            target.shape_rectangles(
                shape.SO.Set,
                shape.SK.Bounding,
                ordering,
                0,
                0,
                rects,
            )
            try:
                target.shape_rectangles(
                    shape.SO.Set,
                    shape.SK.Input,
                    ordering,
                    0,
                    0,
                    rects,
                )
            except Exception:
                pass
        dpy.sync()
    except Exception:
        pass


def _enable_transparency(root, canvas) -> str:
    """Return canvas background color after enabling the best transparency mode."""
    if sys.platform == "darwin":
        try:
            root.configure(bg="systemTransparent")
            canvas.configure(bg="systemTransparent")
            return "systemTransparent"
        except Exception:
            pass
    if sys.platform.startswith("win"):
        try:
            root.configure(bg=KEY)
            root.attributes("-transparentcolor", KEY)
            canvas.configure(bg=KEY)
            return KEY
        except Exception:
            pass
    # Linux: chroma is not supported; X11 shape punches out the rectangle.
    root.configure(bg=KEY)
    canvas.configure(bg=KEY)
    return KEY


def run_pill(
    *,
    amplitude_path: Path,
    control_path: Path,
    position_path: Path,
    phase_path: Path | None = None,
) -> int:
    try:
        import tkinter as tk
    except Exception as exc:
        print(f"[vaani] indicator unavailable: {exc}", file=sys.stderr)
        while True:
            time.sleep(60)
        return 0

    wave = WaveformBuffer(bars=BAR_COUNT)
    phase_file = phase_path
    t0 = time.monotonic()
    root = tk.Tk()
    root.title("Vaani")
    root.overrideredirect(True)
    try:
        root.attributes("-topmost", True)
    except tk.TclError:
        pass

    screen_w = root.winfo_screenwidth()
    screen_h = root.winfo_screenheight()
    saved = _load_position(position_path)
    if saved is not None:
        x, y = saved
        if y < 0:
            y = _default_y(screen_h)
    else:
        x = max(0, (screen_w - WIDTH) // 2)
        y = _default_y(screen_h)
    x = max(0, min(x, max(0, screen_w - WIDTH)))
    y = max(0, min(y, max(0, screen_h - HEIGHT)))
    root.geometry(f"{WIDTH}x{HEIGHT}+{x}+{y}")

    canvas = tk.Canvas(
        root,
        width=WIDTH,
        height=HEIGHT,
        highlightthickness=0,
        bd=0,
    )
    canvas.pack()
    bg = _enable_transparency(root, canvas)

    drag_state: dict[str, int | tuple[int, int] | None] = {
        "start": None,
        "origin_x": None,
        "origin_y": None,
    }
    use_shape = sys.platform.startswith("linux")

    def draw() -> None:
        canvas.delete("all")
        shape_rects: list[tuple[int, int, int, int]] = []

        cx, cy, r = 18, HEIGHT // 2, 12
        canvas.create_oval(cx - r, cy - r, cx + r, cy + r, fill=CANCEL_FILL, outline="")
        canvas.create_line(cx - 4, cy - 4, cx + 4, cy + 4, fill=CANCEL_X, width=1.7, capstyle="round")
        canvas.create_line(cx + 4, cy - 4, cx - 4, cy + 4, fill=CANCEL_X, width=1.7, capstyle="round")
        shape_rects.extend(_circle_rects(cx, cy, r + 1))

        phase = read_phase(phase_file) if phase_file is not None else "recording"
        if phase == "processing":
            t = time.monotonic() - t0
            for i in range(3):
                pulse = 0.35 + 0.65 * abs(math.sin(t * 2.6 + i * 0.9))
                gray = int(160 + 70 * pulse)
                color = f"#{gray:02x}{gray:02x}{min(255, gray + 8):02x}"
                dx = int(WIDTH / 2 - 16 + i * 16)
                pr = int(2.4 + 1.1 * pulse)
                canvas.create_oval(dx - pr, cy - pr, dx + pr, cy + pr, fill=color, outline="")
                shape_rects.extend(_circle_rects(dx, cy, pr + 1))
            if use_shape:
                _apply_x11_shape(root, shape_rects)
            return

        kx, kr = WIDTH - 18, 12
        canvas.create_oval(kx - kr, cy - kr, kx + kr, cy + kr, fill=CONFIRM_FILL, outline="")
        canvas.create_line(kx - 4, cy, kx - 1, cy + 3.4, fill=CONFIRM_MARK, width=1.7, capstyle="round")
        canvas.create_line(kx - 1, cy + 3.4, kx + 4.5, cy - 3.4, fill=CONFIRM_MARK, width=1.7, capstyle="round")
        shape_rects.extend(_circle_rects(kx, cy, kr + 1))

        samples = wave.bars_now()
        inner_left, inner_right = 40, WIDTH - 40
        span = inner_right - inner_left
        max_h = HEIGHT - 14
        for i, level in enumerate(samples):
            bx = inner_left + (i + 0.5) * span / BAR_COUNT
            bar_h = max(2.5, float(level) * max_h)
            half = 1.45
            y0 = cy - bar_h / 2
            y1 = cy + bar_h / 2
            canvas.create_oval(bx - half, y0 - half, bx + half, y0 + half, fill=WAVE, outline="")
            canvas.create_oval(bx - half, y1 - half, bx + half, y1 + half, fill=WAVE, outline="")
            canvas.create_rectangle(bx - half, y0, bx + half, y1, fill=WAVE, outline="")
            shape_rects.append((int(bx - half - 1), int(y0 - 1), int(2 * half + 2), int(bar_h + 2)))

        if use_shape:
            _apply_x11_shape(root, shape_rects)

    def tick() -> None:
        phase = read_phase(phase_file) if phase_file is not None else "recording"
        if phase == "recording":
            try:
                level = float(amplitude_path.read_text(encoding="utf-8").strip())
                wave.push(level)
            except Exception:
                wave.push(0.0)
        draw()
        root.after(33, tick)

    def on_press(event: tk.Event) -> None:
        phase = read_phase(phase_file) if phase_file is not None else "recording"
        if event.x < HIT_PAD:
            _save_position(position_path, root.winfo_x(), root.winfo_y())
            _send(control_path, "cancel")
            root.destroy()
            return
        if phase != "processing" and event.x > WIDTH - HIT_PAD:
            _save_position(position_path, root.winfo_x(), root.winfo_y())
            _send(control_path, "stop")
            root.destroy()
            return
        drag_state["start"] = (event.x_root, event.y_root)
        drag_state["origin_x"] = root.winfo_x()
        drag_state["origin_y"] = root.winfo_y()

    def on_drag(event: tk.Event) -> None:
        start = drag_state["start"]
        origin_x = drag_state["origin_x"]
        origin_y = drag_state["origin_y"]
        if not isinstance(start, tuple) or not isinstance(origin_x, int) or not isinstance(origin_y, int):
            return
        sx, sy = start
        nx = origin_x + event.x_root - sx
        ny = origin_y + event.y_root - sy
        nx = max(0, min(nx, max(0, root.winfo_screenwidth() - WIDTH)))
        ny = max(0, min(ny, max(0, root.winfo_screenheight() - HEIGHT)))
        root.geometry(f"+{nx}+{ny}")

    def on_release(_event: tk.Event) -> None:
        if drag_state["start"] is not None:
            _save_position(position_path, root.winfo_x(), root.winfo_y())
        drag_state["start"] = None
        drag_state["origin_x"] = None
        drag_state["origin_y"] = None

    canvas.bind("<ButtonPress-1>", on_press)
    canvas.bind("<B1-Motion>", on_drag)
    canvas.bind("<ButtonRelease-1>", on_release)

    try:
        root.lift()
        root.attributes("-topmost", True)
        if sys.platform == "darwin":
            root.update_idletasks()
            root.deiconify()
    except tk.TclError:
        pass

    # Silence unused-bg lint when chroma is only for shape fallback.
    _ = bg
    draw()
    root.after(33, tick)
    print("[vaani] indicator pill ready bottom", flush=True)
    root.mainloop()
    return 0
