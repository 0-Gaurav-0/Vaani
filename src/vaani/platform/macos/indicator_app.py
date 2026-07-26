"""Compact bottom recording pill for macOS (reference aesthetic).

Black stadium, grey X, live waveform, white check. Pinned near the bottom.
While Groq is working the same pill switches to a processing animation.
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
from pathlib import Path

# Compact + flush to the absolute screen bottom (may sit over the Dock edge).
WIDTH = 148
CONFIRM_WIDTH = 220
HEIGHT = 28
MARGIN_BOTTOM = 0
BAR_COUNT = 15


def _paths() -> tuple[Path, Path, Path, Path]:
    from ...indicator_protocol import resolve_control_path, resolve_phase_path

    amp = Path(os.environ.get("VAANI_AMPLITUDE_PATH", "/tmp/vaani-amplitude"))
    control = resolve_control_path()
    phase = resolve_phase_path()
    pos = (
        Path.home()
        / "Library"
        / "Application Support"
        / "Vaani"
        / "indicator_bottom.json"
    )
    return amp, control, phase, pos


def _load_x(path: Path) -> float | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return float(data["x"])
    except Exception:
        return None


def _save_x(path: Path, x: float) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"x": int(x), "edge": "bottom"}), encoding="utf-8"
        )
    except Exception:
        pass


def _send(control: Path, action: str) -> None:
    try:
        from ...indicator_protocol import write_command

        write_command(control, action)
    except Exception:
        pass


def _transform_to_foreground() -> None:
    try:
        from ctypes import Structure, byref, c_uint32, cdll

        class ProcessSerialNumber(Structure):
            _fields_ = [("highLongOfPSN", c_uint32), ("lowLongOfPSN", c_uint32)]

        lib = cdll.LoadLibrary(
            "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
        )
        lib.TransformProcessType(byref(ProcessSerialNumber(0, 2)), 1)
    except Exception:
        pass


def _run_appkit(
    amp_path: Path, control_path: Path, phase_path: Path, pos_path: Path
) -> int:
    import objc
    from AppKit import (
        NSApplication,
        NSApplicationActivationPolicyAccessory,
        NSBackingStoreBuffered,
        NSBezierPath,
        NSColor,
        NSEvent,
        NSMakeRect,
        NSScreen,
        NSStatusBar,
        NSStatusWindowLevel,
        NSVariableStatusItemLength,
        NSView,
        NSWindow,
        NSWindowStyleMaskBorderless,
    )
    from Foundation import NSObject, NSTimer
    from PyObjCTools import AppHelper

    from ...indicator_protocol import read_phase
    from ...waveform import WaveformBuffer

    wave = WaveformBuffer(bars=BAR_COUNT)
    ui = {"phase": "recording", "t0": time.monotonic(), "width": WIDTH}

    def _width_for(phase: str) -> int:
        return CONFIRM_WIDTH if phase == "confirming" else WIDTH

    class PillView(NSView):
        def initWithFrame_(self, frame):  # noqa: N802
            self = objc.super(PillView, self).initWithFrame_(frame)
            if self is None:
                return None
            self._drag_start = None
            self._origin_x = None
            return self

        def isFlipped(self):  # noqa: N802
            return True

        def drawRect_(self, _rect):  # noqa: N802
            w, h = int(ui["width"]), HEIGHT
            phase = ui["phase"]
            busy = phase in {"processing", "working"}
            confirming = phase == "confirming"
            # Black stadium
            NSColor.colorWithCalibratedRed_green_blue_alpha_(0.0, 0.0, 0.0, 0.96).set()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(0.5, 0.5, w - 1, h - 1), h / 2, h / 2
            ).fill()
            NSColor.colorWithCalibratedRed_green_blue_alpha_(0.16, 0.16, 0.16, 1.0).set()
            rim = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(0.5, 0.5, w - 1, h - 1), h / 2, h / 2
            )
            rim.setLineWidth_(1.0)
            rim.stroke()

            if confirming:
                # S2 foreshadow: Reject (left) / Approve (right). Clicks wired later.
                NSColor.colorWithCalibratedRed_green_blue_alpha_(0.42, 0.12, 0.12, 1.0).set()
                NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(4, 4, 20, 20)).fill()
                NSColor.colorWithCalibratedRed_green_blue_alpha_(1.0, 0.42, 0.42, 1.0).set()
                reject = NSBezierPath.bezierPath()
                reject.setLineWidth_(1.5)
                reject.setLineCapStyle_(1)
                reject.moveToPoint_((10, 10))
                reject.lineToPoint_((18, 18))
                reject.moveToPoint_((18, 10))
                reject.lineToPoint_((10, 18))
                reject.stroke()

                NSColor.colorWithCalibratedRed_green_blue_alpha_(0.55, 0.55, 0.55, 1.0).set()
                for i in range(3):
                    cx = w / 2 - 10 + i * 10
                    NSBezierPath.bezierPathWithOvalInRect_(
                        NSMakeRect(cx - 1.5, h / 2 - 1.5, 3, 3)
                    ).fill()

                NSColor.colorWithCalibratedRed_green_blue_alpha_(0.12, 0.42, 0.22, 1.0).set()
                NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(w - 24, 4, 20, 20)).fill()
                NSColor.colorWithCalibratedRed_green_blue_alpha_(0.49, 1.0, 0.64, 1.0).set()
                approve = NSBezierPath.bezierPath()
                approve.setLineWidth_(1.5)
                approve.setLineCapStyle_(1)
                approve.setLineJoinStyle_(1)
                approve.moveToPoint_((w - 18, 14))
                approve.lineToPoint_((w - 15, 17))
                approve.lineToPoint_((w - 10, 11))
                approve.stroke()
                return

            # Cancel always available (recording or processing/working)
            NSColor.colorWithCalibratedRed_green_blue_alpha_(0.17, 0.17, 0.18, 1.0).set()
            NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(4, 4, 20, 20)).fill()
            NSColor.whiteColor().set()
            x_path = NSBezierPath.bezierPath()
            x_path.setLineWidth_(1.4)
            x_path.setLineCapStyle_(1)
            x_path.moveToPoint_((10, 10))
            x_path.lineToPoint_((18, 18))
            x_path.moveToPoint_((18, 10))
            x_path.lineToPoint_((10, 18))
            x_path.stroke()

            if busy:
                # Three pulsing dots — "still working on your words"
                t = time.monotonic() - float(ui["t0"])
                for i in range(3):
                    pulse = 0.35 + 0.65 * abs(math.sin(t * 3.2 + i * 0.9))
                    NSColor.colorWithCalibratedRed_green_blue_alpha_(
                        1.0, 1.0, 1.0, pulse
                    ).set()
                    cx = w / 2 - 14 + i * 14
                    r = 2.6 + 1.2 * pulse
                    NSBezierPath.bezierPathWithOvalInRect_(
                        NSMakeRect(cx - r, h / 2 - r, r * 2, r * 2)
                    ).fill()
                return

            # Confirm check (recording only)
            NSColor.whiteColor().set()
            NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(w - 24, 4, 20, 20)).fill()
            NSColor.colorWithCalibratedRed_green_blue_alpha_(0.07, 0.07, 0.07, 1.0).set()
            chk = NSBezierPath.bezierPath()
            chk.setLineWidth_(1.5)
            chk.setLineCapStyle_(1)
            chk.setLineJoinStyle_(1)
            chk.moveToPoint_((w - 18, 14))
            chk.lineToPoint_((w - 15, 17))
            chk.lineToPoint_((w - 10, 11))
            chk.stroke()

            samples = wave.bars_now()
            NSColor.whiteColor().set()
            inner_left, inner_right = 32.0, w - 32.0
            span = inner_right - inner_left
            max_h = h - 6
            for i, level in enumerate(samples):
                bx = inner_left + (i + 0.5) * span / BAR_COUNT
                bar_h = max(2.0, float(level) * max_h)
                NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                    NSMakeRect(bx - 1.2, h / 2 - bar_h / 2, 2.4, bar_h), 1.2, 1.2
                ).fill()

        def mouseDown_(self, event):  # noqa: N802
            loc = self.convertPoint_fromView_(event.locationInWindow(), None)
            x = float(loc.x)
            w = float(ui["width"])
            phase = ui["phase"]
            if phase == "confirming":
                # Placeholder until T2.1 wires approve/reject commands.
                if x < 28 or x > w - 28:
                    return
                self._drag_start = NSEvent.mouseLocation()
                self._origin_x = float(self.window().frame().origin.x)
                return
            if x < 28:
                _send(control_path, "cancel")
                NSApplication.sharedApplication().terminate_(None)
                return
            # Check only stops while recording; ignored while processing/working.
            if phase not in {"processing", "working"} and x > w - 28:
                _send(control_path, "stop")
                NSApplication.sharedApplication().terminate_(None)
                return
            self._drag_start = NSEvent.mouseLocation()
            self._origin_x = float(self.window().frame().origin.x)

        def mouseDragged_(self, _event):  # noqa: N802
            if self._drag_start is None or self._origin_x is None:
                return
            mouse = NSEvent.mouseLocation()
            dx = float(mouse.x) - float(self._drag_start.x)
            # Use full screen frame so the pill can sit under the Dock area.
            screen = NSScreen.mainScreen().frame()
            w = float(ui["width"])
            nx = self._origin_x + dx
            nx = max(
                float(screen.origin.x),
                min(nx, float(screen.origin.x) + float(screen.size.width) - w),
            )
            ny = float(screen.origin.y) + MARGIN_BOTTOM
            self.window().setFrameOrigin_((nx, ny))

        def mouseUp_(self, _event):  # noqa: N802
            if self._drag_start is not None and self.window() is not None:
                _save_x(pos_path, float(self.window().frame().origin.x))
            self._drag_start = None
            self._origin_x = None

    class Delegate(NSObject):
        window = None
        view = None
        status_item = None

        def applicationDidFinishLaunching_(self, _n):  # noqa: N802
            # Quiet menu-bar fallback if the pill is occluded.
            item = NSStatusBar.systemStatusBar().statusItemWithLength_(
                NSVariableStatusItemLength
            )
            if item.button() is not None:
                item.button().setTitle_("●")
                item.button().setToolTip_("Vaani")
            self.status_item = item

            screen = NSScreen.mainScreen().frame()
            saved = _load_x(pos_path)
            if saved is not None:
                x = saved
            else:
                x = float(screen.origin.x) + (float(screen.size.width) - WIDTH) / 2
            y = float(screen.origin.y) + MARGIN_BOTTOM
            win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
                NSMakeRect(x, y, WIDTH, HEIGHT),
                NSWindowStyleMaskBorderless,
                NSBackingStoreBuffered,
                False,
            )
            win.setLevel_(NSStatusWindowLevel)
            win.setOpaque_(False)
            win.setBackgroundColor_(NSColor.clearColor())
            win.setHasShadow_(True)
            win.setHidesOnDeactivate_(False)
            win.setMovableByWindowBackground_(False)
            win.setCollectionBehavior_(1 << 0)  # can join all spaces
            view = PillView.alloc().initWithFrame_(NSMakeRect(0, 0, WIDTH, HEIGHT))
            win.setContentView_(view)
            win.orderFrontRegardless()
            self.window = win
            self.view = view
            print(
                f"[vaani] bottom pill ready x={int(x)} y={int(y)} visible={win.isVisible()}",
                flush=True,
            )
            NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                0.033, self, "tick:", None, True
            )

        def _sync_width(self, phase: str) -> None:
            target = _width_for(phase)
            if int(ui["width"]) == target or self.window is None or self.view is None:
                ui["width"] = target
                return
            ui["width"] = target
            frame = self.window.frame()
            screen = NSScreen.mainScreen().frame()
            nx = float(frame.origin.x)
            max_x = float(screen.origin.x) + float(screen.size.width) - target
            nx = max(float(screen.origin.x), min(nx, max_x))
            self.window.setFrame_display_(
                NSMakeRect(nx, float(frame.origin.y), target, HEIGHT), True
            )
            self.view.setFrame_(NSMakeRect(0, 0, target, HEIGHT))

        def tick_(self, _t):  # noqa: N802
            ui["phase"] = read_phase(phase_path)
            self._sync_width(ui["phase"])
            if ui["phase"] == "recording":
                try:
                    level = float(amp_path.read_text(encoding="utf-8").strip())
                    wave.push(level)
                except Exception:
                    wave.push(0.0)
            if self.status_item is not None and self.status_item.button() is not None:
                if ui["phase"] in {"processing", "working"}:
                    self.status_item.button().setTitle_("…")
                    tip = (
                        "Vaani is working"
                        if ui["phase"] == "working"
                        else "Vaani is transcribing"
                    )
                    self.status_item.button().setToolTip_(tip)
                elif ui["phase"] == "confirming":
                    self.status_item.button().setTitle_("?")
                    self.status_item.button().setToolTip_("Vaani needs confirmation")
                else:
                    self.status_item.button().setTitle_("●")
                    self.status_item.button().setToolTip_("Vaani recording")
            if self.view is not None:
                self.view.setNeedsDisplay_(True)
            if self.window is not None:
                screen = NSScreen.mainScreen().frame()
                frame = self.window.frame()
                target_y = float(screen.origin.y) + MARGIN_BOTTOM
                if abs(float(frame.origin.y) - target_y) > 1:
                    self.window.setFrameOrigin_((float(frame.origin.x), target_y))
                self.window.orderFrontRegardless()

    _transform_to_foreground()
    app = NSApplication.sharedApplication()
    # Accessory avoids a Dock bounce; TransformProcessType still maps windows.
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    app.setDelegate_(Delegate.alloc().init())
    AppHelper.runEventLoop()
    return 0


def main() -> int:
    amp_path, control_path, phase_path, pos_path = _paths()
    print(f"[vaani] indicator start amp={amp_path}", flush=True)
    try:
        return _run_appkit(amp_path, control_path, phase_path, pos_path)
    except Exception as exc:
        print(f"[vaani] AppKit pill failed ({exc!r}); tk fallback", flush=True)
        from ...indicator_tk import run_pill

        return run_pill(
            amplitude_path=amp_path,
            control_path=control_path,
            position_path=pos_path,
            phase_path=phase_path,
        )


if __name__ == "__main__":
    raise SystemExit(main())
