"""Exact X11 passive grabs for Vaani's two recording chords."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable
import re, subprocess, threading

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

    @staticmethod
    def _variants(mods: int) -> tuple[int, ...]:
        lock = X.LockMask if X else 2
        num = X.Mod2Mask if X else 16
        return tuple(mods | l | n for l in (0, lock) for n in (0, num))

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
        if self.display is not None:
            try: self.display.sync()
            except Exception: pass
        self._registrations.clear()
        self._down = False
        self._held_mode = None

    def handle_event(self, event: Any) -> bool:
        if event.type == getattr(X, "KeyRelease", 3):
            if event.detail == self._keycode and self._down:
                self._down = False
                mode = self._held_mode
                self._held_mode = None
                if self.on_release and mode is not None:
                    self.on_release(mode)
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
    """Global listener using xinput test-xi2, avoiding passive-grab conflicts.

    Esc/Enter are observed only while armed via ``set_policy_keys``.
    """
    def __init__(
        self,
        on_trigger: Callable[[str], None],
        on_release: Callable[[str], None] | None = None,
        on_cancel: Callable[[], None] | None = None,
        on_approve: Callable[[], None] | None = None,
    ):
        self.on_trigger, self.on_release = on_trigger, on_release
        self.on_cancel = on_cancel
        self.on_approve = on_approve
        self._proc = None; self._thread = None; self._stop = threading.Event()
        self._down: set[int] = set(); self._triggered = False
        # keycodes: Escape=9, Return=36 (AT Translated Set 2 keyboard)
        self.ctrl, self.super, self.shift, self.space, self.escape = 37, 133, 50, 65, 9
        self.enter = 36
        self._escape_seen = False
        self._enter_seen = False
        self._policy_cancel = False
        self._policy_approve = False

    def register(self) -> None:
        self._stop.clear(); self._thread = threading.Thread(target=self._poll, daemon=True); self._thread.start()

    start = register

    def set_policy_keys(self, *, cancel: bool, approve: bool) -> None:
        """Arm/disarm Esc (cancel) and Enter (approve) observation."""
        self._policy_cancel = bool(cancel)
        self._policy_approve = bool(approve)

    def _poll(self) -> None:
        try:
            device = subprocess.check_output(["xinput", "list", "--id-only", "AT Translated Set 2 keyboard"], text=True).strip()
        except Exception:
            device = "11"
        while not self._stop.is_set():
            try:
                out = subprocess.check_output(["xinput", "query-state", device], text=True, stderr=subprocess.DEVNULL)
                down = {int(c) for c, state in re.findall(r"key\[(\d+)\]=(up|down)", out) if state == "down"}
                if self._policy_cancel or self._policy_approve:
                    if self._policy_cancel:
                        if self.escape in down and not self._escape_seen:
                            self._escape_seen = True
                            if self.on_cancel:
                                self.on_cancel()
                        elif self.escape not in down:
                            self._escape_seen = False
                    else:
                        self._escape_seen = self.escape in down
                    if self._policy_approve:
                        if self.enter in down and not self._enter_seen:
                            self._enter_seen = True
                            if self.on_approve:
                                self.on_approve()
                        elif self.enter not in down:
                            self._enter_seen = False
                    else:
                        self._enter_seen = self.enter in down
                else:
                    # Track held state so re-arming mid-hold does not edge-fire.
                    self._escape_seen = self.escape in down
                    self._enter_seen = self.enter in down
                active = self.space in down and self.ctrl in down and self.super in down
                if active and not self._triggered:
                    self._triggered = True; self.on_trigger(ASSISTANT)
                elif not active and self._triggered:
                    self._triggered = False
                    if self.on_release: self.on_release("smart")
            except Exception: pass
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
        self._proc = None; self._thread = None; self._down.clear(); self._triggered = False
        self._policy_cancel = False
        self._policy_approve = False

    stop = unregister
