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

from .indicator_protocol import (
    read_options,
    read_pending_id,
    read_phase,
    resolve_options_path,
    resolve_pending_path,
)
from .waveform import WaveformBuffer

# Compact + flush to the bottom so it barely eats content space.
WIDTH = 148
CONFIRM_WIDTH = 220
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
    pending_path: Path | None = None,
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
    pending_file = pending_path or resolve_pending_path(
        cache_dir=control_path.parent
    )
    options_file = resolve_options_path(cache_dir=control_path.parent)
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
    layout: dict[str, int] = {"width": WIDTH}

    def _phase() -> str:
        return read_phase(phase_file) if phase_file is not None else "recording"

    def _width_for(phase: str) -> int:
        return CONFIRM_WIDTH if phase == "confirming" else WIDTH

    def _sync_geometry(phase: str) -> None:
        target_w = _width_for(phase)
        if target_w == layout["width"]:
            return
        layout["width"] = target_w
        canvas.config(width=target_w)
        cur_x = root.winfo_x()
        max_x = max(0, root.winfo_screenwidth() - target_w)
        nx = max(0, min(cur_x, max_x))
        root.geometry(f"{target_w}x{HEIGHT}+{nx}+{_bottom_y(root.winfo_screenheight())}")

    def _draw_stadium(w: int) -> None:
        canvas.create_oval(0, 0, HEIGHT, HEIGHT, fill="#000000", outline="#2a2a2a")
        canvas.create_oval(
            w - HEIGHT, 0, w, HEIGHT, fill="#000000", outline="#2a2a2a"
        )
        canvas.create_rectangle(
            HEIGHT // 2, 0, w - HEIGHT // 2, HEIGHT, fill="#000000", outline=""
        )
        canvas.create_line(HEIGHT // 2, 1, w - HEIGHT // 2, 1, fill="#2a2a2a")
        canvas.create_line(
            HEIGHT // 2, HEIGHT - 1, w - HEIGHT // 2, HEIGHT - 1, fill="#2a2a2a"
        )

    def draw() -> None:
        canvas.delete("all")
        phase = _phase()
        w = layout["width"]
        _draw_stadium(w)

        if phase == "confirming":
            canvas.create_text(
                40,
                HEIGHT // 2,
                text="Reject",
                fill="#ff6b6b",
                font=("Helvetica", 9),
            )
            option_labels = read_options(options_file)
            if option_labels:
                # Disambiguation: numbered pill options (no Approve default).
                center = " · ".join(str(i + 1) for i in range(len(option_labels)))
                canvas.create_text(
                    w // 2,
                    HEIGHT // 2,
                    text=center,
                    fill="#ffffff",
                    font=("Helvetica", 9),
                )
            else:
                canvas.create_text(
                    w // 2,
                    HEIGHT // 2,
                    text="Confirm?",
                    fill="#ffffff",
                    font=("Helvetica", 9),
                )
                canvas.create_text(
                    w - 44,
                    HEIGHT // 2,
                    text="Approve",
                    fill="#7dffa3",
                    font=("Helvetica", 9),
                )
            return

        cx, cy, r = 14, HEIGHT // 2, 10
        canvas.create_oval(cx - r, cy - r, cx + r, cy + r, fill="#2c2c2e", outline="")
        canvas.create_line(cx - 3.5, cy - 3.5, cx + 3.5, cy + 3.5, fill="#ffffff", width=1.4)
        canvas.create_line(cx + 3.5, cy - 3.5, cx - 3.5, cy + 3.5, fill="#ffffff", width=1.4)

        if phase in {"processing", "working"}:
            t = time.monotonic() - t0
            for i in range(3):
                pulse = 0.35 + 0.65 * abs(math.sin(t * 3.2 + i * 0.9))
                gray = int(255 * pulse)
                color = f"#{gray:02x}{gray:02x}{gray:02x}"
                dot_x = w / 2 - 14 + i * 14
                dot_r = 2.6 + 1.2 * pulse
                canvas.create_oval(
                    dot_x - dot_r,
                    HEIGHT / 2 - dot_r,
                    dot_x + dot_r,
                    HEIGHT / 2 + dot_r,
                    fill=color,
                    outline="",
                )
            return

        kx, ky, kr = w - 14, HEIGHT // 2, 10
        canvas.create_oval(kx - kr, ky - kr, kx + kr, ky + kr, fill="#ffffff", outline="")
        canvas.create_line(
            kx - 4, ky, kx - 1, ky + 3, fill="#111111", width=1.5, capstyle="round"
        )
        canvas.create_line(
            kx - 1, ky + 3, kx + 4, ky - 3, fill="#111111", width=1.5, capstyle="round"
        )

        samples = wave.bars_now()
        inner_left, inner_right = 32, w - 32
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
        phase = _phase()
        _sync_geometry(phase)
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
        phase = _phase()
        w = layout["width"]
        if phase == "confirming":
            action_id = read_pending_id(pending_file)
            option_labels = read_options(options_file)
            if action_id and event.x < HIT_PAD:
                _send(control_path, f"reject:{action_id}")
                return
            if action_id and option_labels:
                # Map center clicks to option 1..N (left→right).
                inner_left, inner_right = HIT_PAD, w - HIT_PAD
                if inner_left <= event.x <= inner_right:
                    span = max(1, inner_right - inner_left)
                    slot = min(
                        len(option_labels) - 1,
                        max(0, int((event.x - inner_left) / span * len(option_labels))),
                    )
                    _send(control_path, f"select:{action_id}:{slot + 1}")
                    return
            elif action_id and event.x > w - HIT_PAD:
                _send(control_path, f"approve:{action_id}")
                return
            drag_state["start"] = (event.x_root, event.y_root)
            drag_state["origin_x"] = root.winfo_x()
            return
        if event.x < HIT_PAD:
            _send(control_path, "cancel")
            root.destroy()
            return
        if phase not in {"processing", "working"} and event.x > w - HIT_PAD:
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
        w = layout["width"]
        nx = origin_x + event.x_root - sx
        nx = max(0, min(nx, max(0, root.winfo_screenwidth() - w)))
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
