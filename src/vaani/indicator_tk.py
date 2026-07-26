"""Shared tkinter floating recording pill (macOS + Windows).

Aesthetic: compact black stadium at the bottom — grey X, live waveform,
white check to confirm/stop. Waveform tracks mic amplitude.
"""
from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

from .indicator_protocol import read_phase
from .waveform import WaveformBuffer

# Compact + flush to the bottom so it barely eats content space.
WIDTH = 148
HEIGHT = 28
MARGIN_BOTTOM = 0
BAR_COUNT = 15
HIT_PAD = 28  # left/right control hit targets


def _load_position(path: Path) -> int | None:
    """Horizontal position only; vertical is always pinned to the bottom."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return int(data["x"])
    except Exception:
        return None


def _save_position(path: Path, x: int) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"x": int(x), "edge": "bottom"}), encoding="utf-8")
    except Exception:
        pass


def _send(control: Path, action: str) -> None:
    try:
        from .indicator_protocol import write_command

        write_command(control, action)
    except Exception:
        pass


def _bottom_y(screen_h: int) -> int:
    return max(0, screen_h - HEIGHT - MARGIN_BOTTOM)


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
    root.configure(bg="#000000")

    screen_w = root.winfo_screenwidth()
    screen_h = root.winfo_screenheight()
    saved_x = _load_position(position_path)
    x = saved_x if saved_x is not None else max(0, (screen_w - WIDTH) // 2)
    x = max(0, min(x, max(0, screen_w - WIDTH)))
    y = _bottom_y(screen_h)
    root.geometry(f"{WIDTH}x{HEIGHT}+{x}+{y}")

    canvas = tk.Canvas(
        root,
        width=WIDTH,
        height=HEIGHT,
        highlightthickness=0,
        bg="#000000",
        bd=0,
    )
    canvas.pack()

    drag_state: dict[str, int | tuple[int, int] | None] = {
        "start": None,
        "origin_x": None,
    }

    def draw() -> None:
        canvas.delete("all")
        canvas.create_oval(0, 0, HEIGHT, HEIGHT, fill="#000000", outline="#2a2a2a")
        canvas.create_oval(
            WIDTH - HEIGHT, 0, WIDTH, HEIGHT, fill="#000000", outline="#2a2a2a"
        )
        canvas.create_rectangle(
            HEIGHT // 2, 0, WIDTH - HEIGHT // 2, HEIGHT, fill="#000000", outline=""
        )
        canvas.create_line(HEIGHT // 2, 1, WIDTH - HEIGHT // 2, 1, fill="#2a2a2a")
        canvas.create_line(
            HEIGHT // 2, HEIGHT - 1, WIDTH - HEIGHT // 2, HEIGHT - 1, fill="#2a2a2a"
        )

        cx, cy, r = 14, HEIGHT // 2, 10
        canvas.create_oval(cx - r, cy - r, cx + r, cy + r, fill="#2c2c2e", outline="")
        canvas.create_line(cx - 3.5, cy - 3.5, cx + 3.5, cy + 3.5, fill="#ffffff", width=1.4)
        canvas.create_line(cx + 3.5, cy - 3.5, cx - 3.5, cy + 3.5, fill="#ffffff", width=1.4)

        phase = read_phase(phase_file) if phase_file is not None else "recording"
        if phase == "processing":
            t = time.monotonic() - t0
            for i in range(3):
                pulse = 0.35 + 0.65 * abs(math.sin(t * 3.2 + i * 0.9))
                gray = int(255 * pulse)
                color = f"#{gray:02x}{gray:02x}{gray:02x}"
                cx = WIDTH / 2 - 14 + i * 14
                r = 2.6 + 1.2 * pulse
                canvas.create_oval(cx - r, HEIGHT / 2 - r, cx + r, HEIGHT / 2 + r, fill=color, outline="")
            return

        kx, ky, kr = WIDTH - 14, HEIGHT // 2, 10
        canvas.create_oval(kx - kr, ky - kr, kx + kr, ky + kr, fill="#ffffff", outline="")
        canvas.create_line(
            kx - 4, ky, kx - 1, ky + 3, fill="#111111", width=1.5, capstyle="round"
        )
        canvas.create_line(
            kx - 1, ky + 3, kx + 4, ky - 3, fill="#111111", width=1.5, capstyle="round"
        )

        samples = wave.bars_now()
        inner_left, inner_right = 32, WIDTH - 32
        span = inner_right - inner_left
        max_h = HEIGHT - 6
        for i, level in enumerate(samples):
            bx = inner_left + (i + 0.5) * span / BAR_COUNT
            bar_h = max(2.0, float(level) * max_h)
            half = 1.2
            y0 = HEIGHT / 2 - bar_h / 2
            y1 = HEIGHT / 2 + bar_h / 2
            canvas.create_rectangle(
                bx - half, y0, bx + half, y1, fill="#ffffff", outline=""
            )

    def tick() -> None:
        phase = read_phase(phase_file) if phase_file is not None else "recording"
        if phase == "recording":
            try:
                level = float(amplitude_path.read_text(encoding="utf-8").strip())
                wave.push(level)
            except Exception:
                wave.push(0.0)
        y_now = _bottom_y(root.winfo_screenheight())
        if abs(root.winfo_y() - y_now) > 2:
            root.geometry(f"+{root.winfo_x()}+{y_now}")
        draw()
        root.after(33, tick)

    def on_press(event: tk.Event) -> None:
        phase = read_phase(phase_file) if phase_file is not None else "recording"
        if event.x < HIT_PAD:
            _send(control_path, "cancel")
            root.destroy()
            return
        if phase != "processing" and event.x > WIDTH - HIT_PAD:
            _send(control_path, "stop")
            root.destroy()
            return
        drag_state["start"] = (event.x_root, event.y_root)
        drag_state["origin_x"] = root.winfo_x()

    def on_drag(event: tk.Event) -> None:
        start = drag_state["start"]
        origin_x = drag_state["origin_x"]
        if not isinstance(start, tuple) or not isinstance(origin_x, int):
            return
        sx, _sy = start
        nx = origin_x + event.x_root - sx
        nx = max(0, min(nx, max(0, root.winfo_screenwidth() - WIDTH)))
        root.geometry(f"+{nx}+{_bottom_y(root.winfo_screenheight())}")

    def on_release(_event: tk.Event) -> None:
        if drag_state["start"] is not None:
            _save_position(position_path, root.winfo_x())
        drag_state["start"] = None
        drag_state["origin_x"] = None

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

    draw()
    root.after(33, tick)
    print("[vaani] indicator pill ready bottom", flush=True)
    root.mainloop()
    return 0
