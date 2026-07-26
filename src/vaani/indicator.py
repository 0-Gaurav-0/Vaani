"""Recording indicator model and optional GTK pill view."""
from __future__ import annotations
from collections import deque
import math, struct, subprocess, threading, json, os, signal, time
from pathlib import Path

class RecordingIndicator:
    def __init__(self, *, capacity: int = 32, on_cancel=None, on_stop=None):
        self.visible = False; self._levels = deque(maxlen=capacity)
        self.position = (0, 0); self._drag_origin = None
        self.on_cancel, self.on_stop = on_cancel, on_stop
    def start(self): self.visible = True; self._levels.clear()
    def stop(self): self.visible = False
    def update_amplitude(self, amplitude: float):
        self._levels.append(max(0.0, min(1.0, float(amplitude))))
    @property
    def waveform(self): return tuple(self._levels)
    def begin_drag(self, x, y): self._drag_origin = (x, y, *self.position)
    def drag_to(self, x, y):
        if self._drag_origin:
            ox, oy, px, py = self._drag_origin; self.position = (px + x - ox, py + y - oy)
    def end_drag(self): self._drag_origin = None
    def save_position(self, path=None):
        path = Path(path or Path.home()/'.config/vaani/indicator.json'); path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({'x': self.position[0], 'y': self.position[1]}))
    def load_position(self, path=None, bounds=None):
        path = Path(path or Path.home()/'.config/vaani/indicator.json')
        try:
            d=json.loads(path.read_text()); x,y=int(d['x']),int(d['y'])
            if bounds: x=max(0,min(x,max(0,bounds[0]))); y=max(0,min(y,max(0,bounds[1])))
            self.position=(x,y)
        except Exception: pass
    def cancel(self):
        if self.on_cancel: self.on_cancel()
        self.stop()
    def request_stop(self):
        if self.on_stop: self.on_stop()
        self.stop()

class AmplitudeChannel:
    """Per-session scalar channel; only RMS values cross the process boundary."""
    def __init__(self):
        import queue
        self._q=queue.Queue(maxsize=8); self.closed=False
    def publish(self, level: float):
        if self.closed: return
        try: self._q.put_nowait(max(0.0,min(1.0,float(level))))
        except Exception: pass
    def read(self):
        try: return self._q.get_nowait()
        except Exception: return None
    def close(self): self.closed=True
def dispatch_control(action: str, pid: int | None = None):
    """Ask the parent daemon to stop or cancel (file + optional SIGUSR)."""
    try:
        from .indicator_protocol import resolve_control_path, write_command
        write_command(resolve_control_path(), action)
    except Exception:
        pass
    if not hasattr(signal, "SIGUSR1"):
        return True
    target = pid or os.getppid()
    sig = signal.SIGUSR2 if action == "cancel" else signal.SIGUSR1
    try:
        os.kill(target, sig)
    except Exception:
        return False
    return True

def pcm16_rms(data: bytes) -> float:
    """Return normalized microphone level without retaining audio samples."""
    if not data: return 0.0
    n = len(data) // 2
    vals = struct.unpack("<%dh" % n, data[:n * 2])
    return min(1.0, math.sqrt(sum(v * v for v in vals) / n) / 32768.0)

class GtkRecordingIndicator(RecordingIndicator):
    """Best-effort translucent rounded pill; keeps a headless model seam."""
    def __init__(self, **kwargs):
        super().__init__(**kwargs); self.widget = None
        try:
            from gi.repository import Gtk, Gdk
            self.widget = Gtk.Window(); self.widget.set_decorated(False); self.widget.set_resizable(False)
            self.widget.set_default_size(240, 40)
            self.area = Gtk.DrawingArea(); self.area.set_content_width(240); self.area.set_content_height(40)
            self.area.set_draw_func(self._draw)
            self.widget.set_child(self.area)
            click = Gtk.GestureClick(); click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE); click.connect("pressed", self._click)
            self.area.add_controller(click)
            drag = Gtk.GestureDrag(); drag.connect("drag-begin", self._drag_begin)
            self.area.add_controller(drag)
            css = Gtk.CssProvider(); css.load_from_data(b"window { background: transparent; }")
            display = Gdk.Display.get_default()
            Gtk.StyleContext.add_provider_for_display(display, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        except Exception: pass

    def _draw(self, area, cr, width, height):
        def rounded(x, y, w, h, r):
            cr.new_sub_path(); cr.arc(x+w-r, y+r, r, -math.pi/2, 0); cr.arc(x+w-r, y+h-r, r, 0, math.pi/2); cr.arc(x+r, y+h-r, r, math.pi/2, math.pi); cr.arc(x+r, y+r, r, math.pi, 3*math.pi/2); cr.close_path()
        cr.set_source_rgba(.08,.09,.12,.96); rounded(0,0,240,40,20); cr.fill()
        cr.set_source_rgb(.75,.76,.8); cr.arc(22,20,16,0,6.283); cr.fill(); cr.set_source_rgb(.15,.15,.18); cr.set_line_width(2); cr.move_to(17,15); cr.line_to(27,25); cr.move_to(27,15); cr.line_to(17,25); cr.stroke()
        cr.set_source_rgb(.85,.12,.2); cr.arc(218,20,16,0,6.283); cr.fill(); cr.set_source_rgb(1,1,1); cr.rectangle(213,15,10,10); cr.fill()
        levels = self.waveform or (0.0,)
        colors = ((.96,.97,1.0),); samples = (list(levels)[-16:] or [0.0])
        for i in range(16):
            level = samples[i] if i < len(samples) else 0.0; x = 48 + i*144/15
            color = colors[0]; cr.set_source_rgba(*color, .95)
            if level <= 0.01: cr.arc(x, height/2, 2, 0, 6.283); cr.fill()
            else:
                bar_h = max(4, level*(height-8)); rounded(x-1.5, height/2-bar_h/2, 3, bar_h, 1.5); cr.fill()

    def _click(self, gesture, n, x, y):
        action = self.dispatch_control(x, y)
        if action is None and 40 <= x <= 200:
            self._begin_center_move(gesture, n, x, y)

    def _begin_center_move(self, gesture, n, x, y):
        if 40 <= x <= 200:
            try:
                surface = self.widget.get_surface(); surface.get_toplevel().begin_move(
                    gesture.get_device(), gesture.get_current_button() or 1,
                    float(x), float(y), 0)
            except Exception: pass

    def dispatch_control(self, x: float, y: float) -> str | None:
        if x < 40: self.cancel(); return "cancel"
        if x > 200: self.request_stop(); return "stop"
        return None

    def _drag_begin(self, gesture, start_x, start_y):
        """Hand the drag to the compositor using the actual press location."""
        try:
            surface = self.widget.get_surface(); toplevel = surface.get_toplevel()
            device = gesture.get_device(); button = gesture.get_current_button() or 1
            toplevel.begin_move(device, button, float(start_x), float(start_y), 0)
        except Exception: pass

    def update_amplitude(self, amplitude: float):
        super().update_amplitude(amplitude)
        if self.widget and hasattr(self, "area"): self.area.queue_draw()
    def start(self):
        super().start()
        # GTK4 windows are presented by the application after activation.
    def stop(self):
        super().stop()
        if self.widget: self.widget.hide()


def main() -> None:
    """Standalone GTK indicator (legacy). Prefer ``linux.indicator_app`` (tk)."""
    try:
        from gi.repository import Gtk, GLib
        from .indicator_protocol import read_phase, resolve_phase_path

        app = Gtk.Application(application_id="com.vaani.RecordingIndicator")
        phase_path = resolve_phase_path()

        def activate(application):
            indicator = GtkRecordingIndicator(
                on_cancel=lambda: dispatch_control("cancel"),
                on_stop=lambda: dispatch_control("stop"),
            )
            indicator.start()
            indicator.widget.set_application(application)
            indicator.widget.present()
            # Do not open a second parec stream: PulseAudio source contention
            # can starve the primary recorder and produce silence transcripts.
            t0 = time.monotonic()

            def refresh():
                try:
                    phase = read_phase(phase_path)
                    if phase == "processing":
                        # Animate a gentle pulse so the pill is not dismissed.
                        pulse = 0.35 + 0.65 * abs(math.sin((time.monotonic() - t0) * 3.2))
                        indicator.update_amplitude(pulse)
                    else:
                        amp = Path(os.environ.get("VAANI_AMPLITUDE_PATH", "/tmp/vaani-amplitude"))
                        level = float(amp.read_text())
                        indicator.update_amplitude(level)
                except Exception:
                    pass
                return bool(indicator.visible)

            GLib.timeout_add(50, refresh)

            def cleanup(*_):
                return False

            indicator.widget.connect("close-request", cleanup)

        app.connect("activate", activate)
        app.run([])
    except Exception:
        # On headless systems the process remains alive so lifecycle semantics
        # are consistent; parent termination still stops it deterministically.
        def _exit(*_):
            raise SystemExit(0)

        signal.signal(signal.SIGTERM, _exit)
        while True:
            time.sleep(60)


if __name__ == "__main__":
    main()
