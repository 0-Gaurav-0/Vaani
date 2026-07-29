"""Exact X11 passive grabs for Vaani's two recording chords."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable
import re, select, subprocess, threading

try:
    from Xlib import X, XK, error
except Exception:  # pragma: no cover
    X = XK = error = None

SMART = "smart"
LITERAL = "literal"
ASSISTANT = "assistant"

@dataclass(frozen=True)
class HotkeyRegistration:
    mode: str
    keycode: int
    modifiers: int

class HotkeyManager:
    def __init__(self, on_trigger: Callable[[str], None], display: Any | None = None,
                 on_release: Callable[[str], None] | None = None):
        self.on_trigger, self.on_release, self.display = on_trigger, on_release, display
        self.root = display.screen().root if display is not None else None
        self._registrations: list[HotkeyRegistration] = []
        self._down = False
        self._held_mode: str | None = None
        self._keycode = None
        self._autorepeat_disabled = False

    @staticmethod
    def _variants(mods: int) -> tuple[int, ...]:
        lock = X.LockMask if X else 2
        num = X.Mod2Mask if X else 16
        return tuple(mods | l | n for l in (0, lock) for n in (0, num))

    def _set_space_autorepeat(self, enabled: bool) -> None:
        """Per-key autorepeat off while grabs are live (python-xlib has no XKB module)."""
        if self.display is None or self._keycode is None or X is None:
            return
        mode = X.AutoRepeatModeOn if enabled else X.AutoRepeatModeOff
        try:
            self.display.change_keyboard_control(key=self._keycode, auto_repeat_mode=mode)
            self.display.sync()
            self._autorepeat_disabled = not enabled
        except Exception:
            pass

    def register(self) -> None:
        if self.display is None: raise RuntimeError("X11 display unavailable")
        self._keycode = self.display.keysym_to_keycode(XK.string_to_keysym("space"))
        # VibeTyper-style toggle: Ctrl+Space starts/stops recording.
        specs = [(SMART, X.ControlMask), (LITERAL, X.ControlMask | X.ShiftMask),
                 (ASSISTANT, X.ControlMask | X.Mod1Mask)]
        made: list[HotkeyRegistration] = []
        try:
            for mode, mods in specs:
                for variant in self._variants(mods):
                    self.root.grab_key(self._keycode, variant, True, X.GrabModeAsync, X.GrabModeAsync)
                    made.append(HotkeyRegistration(mode, self._keycode, variant))
            self.display.sync()
            # Prevent X from synthesizing Release+Press pairs while Space is held.
            # The peek heuristic alone is unreliable on some compositors (timestamps
            # differ / other events interleave), which thrashes hold-to-talk.
            self._set_space_autorepeat(False)
        except Exception as exc:
            for r in made:
                try: self.root.ungrab_key(r.keycode, r.modifiers)
                except Exception: pass
            self._registrations.clear()
            raise RuntimeError("shortcut registration failed (possibly already in use)") from exc
        self._registrations = made

    def unregister(self) -> None:
        for r in self._registrations:
            try: self.root.ungrab_key(r.keycode, r.modifiers)
            except Exception: pass
        if self._autorepeat_disabled:
            self._set_space_autorepeat(True)
        if self.display is not None:
            try: self.display.sync()
            except Exception: pass
        self._registrations.clear()
        self._down = False
        self._held_mode = None

    def _key_is_down(self, keycode: int) -> bool:
        """Read the server keymap bit for ``keycode`` (XQueryKeymap)."""
        if self.display is None:
            return False
        try:
            keymap = self.display.query_keymap()
            return bool(keymap[keycode // 8] & (1 << (keycode % 8)))
        except Exception:
            return False

    def poll_release(self) -> None:
        """Stop hold-to-talk when Space is physically up (backup for missed KeyRelease).

        Passive XGrabKey on some compositors delivers KeyPress reliably but drops or
        delays KeyRelease. The main loop calls this on a short timeout so release
        still stops recording without needing the pill's Stop button.
        """
        if not self._down or self._keycode is None:
            return
        if not self._key_is_down(self._keycode):
            self._fire_release()

    def _fire_release(self) -> None:
        if not self._down:
            return
        self._down = False
        mode = self._held_mode
        self._held_mode = None
        if self.on_release and mode is not None:
            self.on_release(mode)

    def _is_autorepeat(self, release_event: Any) -> bool:
        """Peek for a synthetic KeyPress that follows a held-key auto-repeat Release.

        Skipped when per-key autorepeat is already disabled. Defense in depth
        otherwise: any same-keycode KeyPress within the peek window counts.
        """
        if self._autorepeat_disabled or self.display is None:
            return False
        try:
            if not self.display.pending_events():
                fd = self.display.fileno()
                ready, _, _ = select.select([fd], [], [], 0.03)
                if not ready or not self.display.pending_events():
                    return False
            nxt = self.display.next_event()
        except Exception:
            return False
        if nxt.type == getattr(X, "KeyPress", 2) and nxt.detail == release_event.detail:
            return True
        # Not a repeat pair; this buffered event still needs handling.
        self.handle_event(nxt)
        return False

    def handle_event(self, event: Any) -> bool:
        if event.type == getattr(X, "KeyRelease", 3):
            if event.detail == self._keycode and self._down:
                if self._is_autorepeat(event):
                    return False
                self._fire_release()
            return False
        if event.type != getattr(X, "KeyPress", 2) or event.detail != self._keycode:
            return False
        if self._down: return False
        self._down = True
        state = int(getattr(event, "state", 0))
        clean = state & ~(X.LockMask | X.Mod2Mask)
        mode = None
        if clean == X.ControlMask: mode = SMART
        elif clean == (X.ControlMask | X.ShiftMask): mode = LITERAL
        elif clean == (X.ControlMask | X.Mod1Mask): mode = ASSISTANT
        if mode:
            self._held_mode = mode
            self.on_trigger(mode)
            return True
        self._down = False
        return False

    # Names convenient for event-loop adapters.
    handle = handle_event
    start = register
    stop = unregister

def open_hotkeys(callback: Callable[[str], None]) -> HotkeyManager:
    from Xlib.display import Display
    manager = HotkeyManager(callback, Display()); manager.register(); return manager

HotkeyRegistrar = HotkeyManager


class XInputHotkeyManager:
    """Hold-to-talk via ``xinput query-state`` (no XGrabKey, no GNOME shortcut).

    Polls the physical keyboard device so Ctrl+Space works even when passive
    grabs are unreliable or stolen by the desktop keybinding daemon.
    """
    def __init__(self, on_trigger: Callable[[str], None], on_release: Callable[[str], None] | None = None,
                 on_cancel: Callable[[], None] | None = None):
        self.on_trigger, self.on_release, self.on_cancel = on_trigger, on_release, on_cancel
        self._proc = None; self._thread = None; self._stop = threading.Event()
        self._down: set[int] = set(); self._triggered = False
        self._held_mode: str | None = None
        self._session_active = False
        # Defaults match a typical PC AT keyboard; refined at register() time.
        self.ctrl_keys = {37, 105}   # Control_L / Control_R
        self.shift_keys = {50, 62}   # Shift_L / Shift_R
        self.alt_keys = {64, 108}    # Alt_L / Alt_R
        self.super_keys = {133, 134} # Super_L / Super_R
        self.space, self.escape = 65, 9
        self._escape_seen = False

    def _resolve_keycodes(self) -> None:
        try:
            from Xlib import display, XK
            dpy = display.Display()
            def kc(name: str) -> int | None:
                try:
                    code = dpy.keysym_to_keycode(XK.string_to_keysym(name))
                    return int(code) if code else None
                except Exception:
                    return None
            ctrls = {c for c in (kc("Control_L"), kc("Control_R")) if c}
            shifts = {c for c in (kc("Shift_L"), kc("Shift_R")) if c}
            alts = {c for c in (kc("Alt_L"), kc("Alt_R"), kc("Meta_L"), kc("Meta_R")) if c}
            supers = {c for c in (kc("Super_L"), kc("Super_R")) if c}
            space = kc("space")
            escape = kc("Escape")
            if ctrls: self.ctrl_keys = ctrls
            if shifts: self.shift_keys = shifts
            if alts: self.alt_keys = alts
            if supers: self.super_keys = supers
            if space: self.space = space
            if escape: self.escape = escape
            # Keep legacy attrs used by older tests / callers.
            self.ctrl = next(iter(self.ctrl_keys))
            self.shift = next(iter(self.shift_keys))
            self.super = next(iter(self.super_keys)) if self.super_keys else 133
            dpy.close()
        except Exception:
            self.ctrl = 37
            self.shift = 50
            self.super = 133

    def register(self) -> None:
        self._resolve_keycodes()
        self._stop.clear()
        self._thread = threading.Thread(target=self._poll, daemon=True)
        self._thread.start()

    start = register

    def _chord_mode(self, down: set[int]) -> str | None:
        space = self.space in down
        ctrl = bool(down & self.ctrl_keys)
        shift = bool(down & self.shift_keys)
        alt = bool(down & self.alt_keys)
        super_ = bool(down & self.super_keys)
        if not (space and ctrl):
            return None
        # Ctrl+Super+Space kept as an assistant alias (historical).
        if (alt or super_) and not shift:
            return ASSISTANT
        if shift and not alt:
            return LITERAL
        if not shift and not alt:
            return SMART
        return None

    def _poll(self) -> None:
        try:
            device = subprocess.check_output(
                ["xinput", "list", "--id-only", "AT Translated Set 2 keyboard"],
                text=True,
            ).strip()
        except Exception:
            device = "11"
        while not self._stop.is_set():
            try:
                out = subprocess.check_output(
                    ["xinput", "query-state", device],
                    text=True,
                    stderr=subprocess.DEVNULL,
                )
                down = {
                    int(c)
                    for c, state in re.findall(r"key\[(\d+)\]=(up|down)", out)
                    if state == "down"
                }
                if self.escape in down and not self._escape_seen:
                    self._escape_seen = True
                    if self.on_cancel:
                        self.on_cancel()
                elif self.escape not in down:
                    self._escape_seen = False
                mode = self._chord_mode(down)
                if mode and not self._triggered:
                    self._held_mode = mode
                    self._triggered = True
                    started = False
                    try:
                        started = bool(self.on_trigger(mode))
                    except Exception:
                        started = False
                    self._session_active = started
                elif not mode and self._triggered:
                    was_active = getattr(self, "_session_active", True)
                    held = self._held_mode or SMART
                    self._triggered = False
                    self._session_active = False
                    self._held_mode = None
                    if was_active and self.on_release:
                        self.on_release(held)
            except Exception:
                pass
            self._stop.wait(0.06)

    def _read(self) -> None:
        detail = None
        while self._proc and self._proc.stdout and not self._stop.is_set():
            line = self._proc.stdout.readline()
            if not line: break
            m = re.search(r"EVENT type (2|3) ", line)
            if m: detail = int(m.group(1)); continue
            m = re.search(r"detail:\s*(\d+)", line)
            if not m or detail is None: continue
            kind, code = detail, int(m.group(1)); detail = None
            if kind == 2:
                self._down.add(code)
                if code == self.space and self.ctrl in self._down and self.super in self._down and not self._triggered:
                    self._triggered = True
                    self.on_trigger(ASSISTANT)
            else:
                self._down.discard(code)
                if code == self.space and self._triggered:
                    self._triggered = False
                    if self.on_release: self.on_release("smart")

    def unregister(self) -> None:
        self._stop.set()
        if self._proc:
            self._proc.terminate()
            try: self._proc.wait(timeout=1)
            except subprocess.TimeoutExpired: self._proc.kill()
        self._proc = None; self._thread = None; self._down.clear()
        self._triggered = False
        self._held_mode = None
        self._session_active = False

    stop = unregister


class MiddleButtonGesture:
    """Single-hold → smart; short-click then press-hold → assistant; short click alone → no-op."""

    def __init__(
        self,
        *,
        hold_ms: float = 150.0,
        double_ms: float = 350.0,
        clock: Callable[[], float] | None = None,
    ):
        import time as _time

        self.hold_ms = hold_ms
        self.double_ms = double_ms
        self._clock = clock or _time.monotonic
        self.reset()

    def reset(self) -> None:
        self.down = False
        self.down_at = 0.0
        self.armed_until = -1.0
        self.mode: str | None = None
        self.active = False

    def on_down(self) -> str | None:
        """Return mode to start immediately, if any."""
        now = self._clock()
        self.down = True
        self.down_at = now
        if self.armed_until >= 0.0 and now <= self.armed_until:
            self.armed_until = -1.0
            self.mode = ASSISTANT
            self.active = True
            return ASSISTANT
        return None

    def on_up(self) -> bool:
        """Return True when an active session should stop."""
        now = self._clock()
        was_active = self.active
        held_ms = (now - self.down_at) * 1000.0 if self.down else 0.0
        self.down = False
        if was_active:
            self.active = False
            self.mode = None
            self.armed_until = -1.0
            return True
        if held_ms < self.hold_ms:
            self.armed_until = now + (self.double_ms / 1000.0)
        else:
            self.armed_until = -1.0
        return False

    def tick(self) -> str | None:
        """While held, promote a pending single press into smart after hold_ms."""
        if self.active or not self.down:
            return None
        now = self._clock()
        if (now - self.down_at) * 1000.0 >= self.hold_ms:
            self.mode = SMART
            self.active = True
            self.armed_until = -1.0
            return SMART
        return None


class MiddleButtonHotkeyManager:
    """ThinkPad middle-button hold-to-talk (owns button 2 while Vaani runs)."""

    TRACKPOINT_NAMES = (
        "Elan TrackPoint",
        "TPPS/2 Elan TrackPoint",
        "TPPS/2 IBM TrackPoint",
        "ThinkPad TrackPoint",
    )

    def __init__(
        self,
        on_trigger: Callable[[str], None],
        on_release: Callable[[str], None] | None = None,
        on_cancel: Callable[[], None] | None = None,
        *,
        hold_ms: float = 150.0,
        double_ms: float = 350.0,
    ):
        self.on_trigger = on_trigger
        self.on_release = on_release
        self.on_cancel = on_cancel
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._gesture = MiddleButtonGesture(hold_ms=hold_ms, double_ms=double_ms)
        self._pointer_id: str | None = None
        self._keyboard_id: str | None = None
        self._saved_scroll_button: str | None = None
        self._escape = 9
        self._escape_seen = False
        self._session_active = False
        self._held_mode: str | None = None
        self._ignore_until_up = False

    def _xinput(self, *args: str) -> str:
        return subprocess.check_output(["xinput", *args], text=True, stderr=subprocess.DEVNULL)

    def _resolve_devices(self) -> None:
        try:
            listing = self._xinput("list")
        except Exception:
            listing = ""
        pointer = None
        for name in self.TRACKPOINT_NAMES:
            if name in listing:
                try:
                    pointer = self._xinput("list", "--id-only", name).strip().splitlines()[0]
                    break
                except Exception:
                    continue
        # Do not fall back to an arbitrary pointer: that could resolve the
        # touchpad and turn its synthetic middle clicks into Vaani triggers.
        self._pointer_id = pointer
        try:
            self._keyboard_id = self._xinput(
                "list", "--id-only", "AT Translated Set 2 keyboard"
            ).strip().splitlines()[0]
        except Exception:
            self._keyboard_id = "11"
        try:
            from Xlib import display, XK

            dpy = display.Display()
            code = dpy.keysym_to_keycode(XK.string_to_keysym("Escape"))
            if code:
                self._escape = int(code)
            dpy.close()
        except Exception:
            self._escape = 9

    def _trackpoint_middle_state(self) -> bool | None:
        """Return button-2 state for the resolved TrackPoint, or None if unknown."""
        if not self._pointer_id:
            return None
        try:
            state = self._xinput("query-state", self._pointer_id)
        except Exception:
            return None
        match = re.search(r"button\[2\]=(down|up)", state)
        if match is None:
            return None
        return match.group(1) == "down"

    def _disable_button_scroll(self) -> None:
        pid = self._pointer_id
        if not pid:
            return
        try:
            props = self._xinput("list-props", pid)
            m = re.search(r"libinput Button Scrolling Button[^:]*:\s*(\d+)", props)
            if m:
                self._saved_scroll_button = m.group(1)
                subprocess.run(
                    ["xinput", "set-prop", pid, "libinput Button Scrolling Button", "0"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        except Exception:
            pass

    def _restore_button_scroll(self) -> None:
        pid = self._pointer_id
        if not pid or self._saved_scroll_button is None:
            return
        try:
            subprocess.run(
                [
                    "xinput",
                    "set-prop",
                    pid,
                    "libinput Button Scrolling Button",
                    self._saved_scroll_button,
                ],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass
        self._saved_scroll_button = None

    def _escape_down(self, dpy=None) -> bool:
        # Prefer XQueryKeymap on the grab display (no subprocess). Fall back to xinput.
        if dpy is not None:
            try:
                data = bytes(dpy.query_keymap())
                kc = int(self._escape)
                if 0 <= kc < len(data) * 8:
                    return bool(data[kc // 8] & (1 << (kc % 8)))
            except Exception:
                pass
        try:
            out = self._xinput("query-state", self._keyboard_id or "11")
        except Exception:
            return False
        return bool(re.search(rf"key\[{self._escape}\]=down", out))

    def _cancel_now(self) -> None:
        """Esc (or equivalent): abort session; only ignore middle-button if it is down."""
        if self.on_cancel:
            try:
                self.on_cancel()
            except Exception:
                pass
        self._session_active = False
        self._held_mode = None
        # Only arm ignore-until-up when button 2 is physically held. Esc while
        # Idle used to set this forever: press was ignored (gesture.down stayed
        # False) so the matching release never cleared the flag → dead hotkey
        # until restart.
        self._ignore_until_up = self._trackpoint_middle_state() is True
        self._gesture.active = False
        self._gesture.mode = None
        self._gesture.armed_until = -1.0

    def _start_mode(self, mode: str) -> None:
        self._held_mode = mode
        started = False
        try:
            started = bool(self.on_trigger(mode))
        except Exception:
            started = False
        self._session_active = started
        if not started:
            # Controller refused (usually still processing). Freeze the gesture
            # until physical release so tick() cannot spam re-trigger.
            self._gesture.active = False
            self._gesture.mode = None
            self._gesture.armed_until = -1.0
            self._ignore_until_up = True
            self._held_mode = None

    def _stop_mode(self) -> None:
        was_active = self._session_active
        held = self._held_mode or SMART
        self._session_active = False
        self._held_mode = None
        if was_active and self.on_release:
            try:
                self.on_release(held)
            except Exception:
                pass

    def _handle_button_event(self, event_type: int) -> bool:
        """Handle button 2 only when it belongs to the physical TrackPoint."""
        press_type = getattr(X, "ButtonPress", 4)
        release_type = getattr(X, "ButtonRelease", 5)
        if event_type == press_type:
            if self._ignore_until_up or self._trackpoint_middle_state() is not True:
                return False
            mode = self._gesture.on_down()
            if mode:
                self._start_mode(mode)
            return True
        if event_type != release_type:
            return False
        # Always clear Esc-armed ignore on a real button-2 release, even when
        # the press was ignored (gesture.down False) — otherwise the hotkey dies.
        self._ignore_until_up = False
        if not self._gesture.down:
            return False
        if self._trackpoint_middle_state() is True:
            # A touchpad middle-click can arrive while TrackPoint button 2 is
            # held. Do not let that unrelated release stop hold-to-talk.
            return False
        # Unknown state is safe to release: it can stop an accepted physical
        # press, but can never start a session or leave the microphone stuck.
        if self._gesture.on_up():
            self._stop_mode()
        else:
            self._gesture.down = False
        return True

    def _run(self) -> None:
        if X is None:
            return
        from Xlib import display
        from Xlib import X as Xconst

        dpy = display.Display()
        root = dpy.screen().root
        event_mask = Xconst.ButtonPressMask | Xconst.ButtonReleaseMask
        grabbed_mods: list[int] = []
        for mods in (
            0,
            Xconst.LockMask,
            Xconst.Mod2Mask,
            Xconst.LockMask | Xconst.Mod2Mask,
            Xconst.ControlMask,
            Xconst.ShiftMask,
            Xconst.Mod1Mask,
            Xconst.Mod4Mask,
            getattr(Xconst, "AnyModifier", 1 << 15),
        ):
            try:
                root.grab_button(
                    2,
                    mods,
                    False,
                    event_mask,
                    Xconst.GrabModeAsync,
                    Xconst.GrabModeAsync,
                    Xconst.NONE,
                    Xconst.NONE,
                )
                grabbed_mods.append(mods)
            except Exception:
                continue
        try:
            dpy.sync()
        except Exception:
            pass

        self._ignore_until_up = False
        try:
            while not self._stop.is_set():
                try:
                    if not self._ignore_until_up:
                        mode = self._gesture.tick()
                        if mode:
                            self._start_mode(mode)

                    esc = self._escape_down(dpy)
                    if esc and not self._escape_seen:
                        self._escape_seen = True
                        self._cancel_now()
                    elif not esc:
                        self._escape_seen = False

                    readable, _, _ = select.select([dpy.fileno()], [], [], 0.03)
                    if not (readable or dpy.pending_events()):
                        continue
                    while dpy.pending_events():
                        ev = dpy.next_event()
                        detail = getattr(ev, "detail", None)
                        if detail != 2:
                            continue
                        self._handle_button_event(ev.type)
                except Exception:
                    if self._stop.wait(0.05):
                        break
        finally:
            for mods in grabbed_mods:
                try:
                    root.ungrab_button(2, mods)
                except Exception:
                    pass
            try:
                dpy.sync()
            except Exception:
                pass
            try:
                dpy.close()
            except Exception:
                pass

    def register(self) -> None:
        self._resolve_devices()
        self._disable_button_scroll()
        self._gesture.reset()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    start = register

    def unregister(self) -> None:
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            try:
                thread.join(timeout=1.5)
            except Exception:
                pass
        self._restore_button_scroll()
        self._gesture.reset()
        self._session_active = False
        self._held_mode = None
        self._escape_seen = False
        self._ignore_until_up = False

    stop = unregister
