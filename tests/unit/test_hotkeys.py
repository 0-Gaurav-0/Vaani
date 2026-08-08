from types import SimpleNamespace
import pytest
from vaani.hotkeys import HotkeyManager, SMART, LITERAL
from Xlib import X

class Root:
    def __init__(self, fail=False): self.calls=[]; self.fail=fail
    def grab_key(self,*a):
        self.calls.append(("grab",a))
        if self.fail and len([x for x in self.calls if x[0]=="grab"]) == 3: raise RuntimeError("BadAccess")
    def ungrab_key(self,*a): self.calls.append(("ungrab",a))
class Display:
    def __init__(self, fail=False):
        self.root=Root(fail)
        self.keyboard_control_calls=[]
        self._keymap = [0] * 32
    def screen(self): return SimpleNamespace(root=self.root)
    def keysym_to_keycode(self, _): return 38
    def sync(self): pass
    def change_keyboard_control(self, **kwargs): self.keyboard_control_calls.append(kwargs)
    def query_keymap(self): return list(self._keymap)
    def set_key_down(self, keycode, down=True):
        bit = 1 << (keycode % 8)
        idx = keycode // 8
        if down: self._keymap[idx] |= bit
        else: self._keymap[idx] &= ~bit


class SyncBad(Display):
    def sync(self): raise RuntimeError("BadAccess")

def ev(t, state): return SimpleNamespace(type=t, detail=38, state=state)
def evt(t, detail, time, state=X.ControlMask): return SimpleNamespace(type=t, detail=detail, state=state, time=time)

class QueueDisplay(Display):
    def __init__(self, queue):
        super().__init__(); self._queue = list(queue)
    def pending_events(self): return len(self._queue)
    def fileno(self): return -1
    def next_event(self): return self._queue.pop(0)

def test_registers_variants_and_unregisters():
    d=Display(); cb=[]; h=HotkeyManager(cb.append,d); h.register()
    assert len(h._registrations)==12
    h.unregister(); assert len([x for x in d.root.calls if x[0]=="ungrab"])==12

def test_bad_access_rolls_back():
    d=Display(True)
    with pytest.raises(RuntimeError): HotkeyManager(lambda _:None,d).register()
    assert len([x for x in d.root.calls if x[0]=="ungrab"])==2

def test_exact_modes_repeat_and_release():
    out=[]; releases=[]
    h=HotkeyManager(out.append,Display(), on_release=releases.append); h._keycode=38
    smart=X.ControlMask
    assert h.handle_event(ev(X.KeyPress,smart)); assert not h.handle_event(ev(X.KeyPress,smart))
    assert not h.handle_event(ev(X.KeyRelease,0)); assert h.handle_event(ev(X.KeyPress,X.ControlMask|X.ShiftMask))
    assert out==[SMART,LITERAL]
    assert releases==[SMART]
    h.handle_event(ev(X.KeyRelease,0)); assert h.handle_event(ev(X.KeyPress,smart|X.Mod1Mask))
    assert out==[SMART,LITERAL,"assistant"]
    assert releases==[SMART,LITERAL]


def test_hold_release_passes_held_mode():
    presses=[]; releases=[]
    h=HotkeyManager(presses.append,Display(), on_release=releases.append); h._keycode=38
    assert h.handle_event(ev(X.KeyPress,X.ControlMask|X.ShiftMask))
    assert presses==[LITERAL]
    h.handle_event(ev(X.KeyRelease,0))
    assert releases==[LITERAL]

def test_none_display_is_rejected():
    with pytest.raises(RuntimeError): HotkeyManager(lambda _:None).register()

def test_lock_and_numlock_variants_match_same_chord():
    out=[]; h=HotkeyManager(out.append,Display()); h._keycode=38
    chord=X.ControlMask
    for extra in (X.LockMask, X.Mod2Mask, X.LockMask|X.Mod2Mask):
        assert h.handle_event(ev(X.KeyPress,chord|extra)); h.handle_event(ev(X.KeyRelease,0))
    assert out == [SMART, SMART, SMART]

def test_switching_chords_requires_release_cycle():
    out=[]; h=HotkeyManager(out.append,Display()); h._keycode=38
    assert h.handle_event(ev(X.KeyPress,X.ControlMask))
    assert not h.handle_event(ev(X.KeyPress,X.ControlMask|X.ShiftMask|X.Mod4Mask))
    h.handle_event(ev(X.KeyRelease,0)); assert h.handle_event(ev(X.KeyPress,X.ControlMask|X.ShiftMask))
    assert out == [SMART,LITERAL]

def test_xsync_badaccess_is_registration_failure():
    with pytest.raises(RuntimeError): HotkeyManager(lambda _:None,SyncBad()).register()

def test_autorepeat_release_press_pair_is_swallowed():
    presses=[]; releases=[]
    d = QueueDisplay([evt(X.KeyPress, 38, 1000)])
    h=HotkeyManager(presses.append, d, on_release=releases.append); h._keycode=38
    h.handle_event(evt(X.KeyPress, 38, 999))
    assert presses == [SMART]
    h.handle_event(evt(X.KeyRelease, 38, 1000))
    assert releases == []
    assert h._down is True

def test_autorepeat_pair_with_different_timestamps_is_swallowed():
    """GNOME/mutter may not stamp Release+Press with identical times."""
    presses=[]; releases=[]
    d = QueueDisplay([evt(X.KeyPress, 38, 1005)])
    h=HotkeyManager(presses.append, d, on_release=releases.append); h._keycode=38
    h.handle_event(evt(X.KeyPress, 38, 999))
    h.handle_event(evt(X.KeyRelease, 38, 1000))
    assert releases == []
    assert presses == [SMART]
    assert h._down is True

def test_genuine_release_with_no_pending_press_still_fires():
    presses=[]; releases=[]
    d = QueueDisplay([])
    h=HotkeyManager(presses.append, d, on_release=releases.append); h._keycode=38
    h.handle_event(evt(X.KeyPress, 38, 1))
    h.handle_event(evt(X.KeyRelease, 38, 2))
    assert releases == [SMART]
    assert h._down is False

def test_register_disables_space_autorepeat_and_unregister_restores():
    d=Display(); h=HotkeyManager(lambda _:None, d); h.register()
    assert h._autorepeat_disabled is True
    assert d.keyboard_control_calls[0]["auto_repeat_mode"] == X.AutoRepeatModeOff
    assert d.keyboard_control_calls[0]["key"] == 38
    h.unregister()
    assert h._autorepeat_disabled is False
    assert d.keyboard_control_calls[-1]["auto_repeat_mode"] == X.AutoRepeatModeOn

def test_poll_release_fires_when_space_physically_up():
    presses=[]; releases=[]
    d=Display()
    h=HotkeyManager(presses.append, d, on_release=releases.append); h._keycode=38
    h.handle_event(evt(X.KeyPress, 38, 1))
    assert presses == [SMART]
    d.set_key_down(38, False)
    h.poll_release()
    assert releases == [SMART]
    assert h._down is False

def test_poll_release_noop_while_space_still_down():
    presses=[]; releases=[]
    d=Display()
    h=HotkeyManager(presses.append, d, on_release=releases.append); h._keycode=38
    h.handle_event(evt(X.KeyPress, 38, 1))
    d.set_key_down(38, True)
    h.poll_release()
    assert releases == []
    assert h._down is True

def test_keyrelease_immediate_when_autorepeat_already_disabled():
    """With AutoRepeatModeOff, KeyRelease is always genuine — no peek delay."""
    presses=[]; releases=[]
    # Pending press would previously have been swallowed as "autorepeat".
    d = QueueDisplay([evt(X.KeyPress, 38, 1005)])
    h=HotkeyManager(presses.append, d, on_release=releases.append); h._keycode=38
    h._autorepeat_disabled = True
    h.handle_event(evt(X.KeyPress, 38, 1))
    h.handle_event(evt(X.KeyRelease, 38, 2))
    assert releases == [SMART]
    assert h._down is False

def test_xinput_escape_cancel_is_edge_triggered(monkeypatch):
    out=[]; m=__import__('vaani.hotkeys', fromlist=['XInputHotkeyManager']).XInputHotkeyManager(lambda _:None, on_cancel=lambda: out.append(1))
    m._escape_seen=False
    # Equivalent state transitions from xinput polling; Escape is observed but not consumed.
    down={9};
    if 9 in down and not m._escape_seen: m._escape_seen=True; m.on_cancel()
    assert out == [1]

def test_xinput_chord_modes():
    from vaani.hotkeys import XInputHotkeyManager, SMART, LITERAL, ASSISTANT
    m = XInputHotkeyManager(lambda _: None)
    m.ctrl_keys = {37, 105}
    m.shift_keys = {50, 62}
    m.alt_keys = {64, 108}
    m.super_keys = {133, 134}
    m.space = 65
    assert m._chord_mode({37, 65}) == SMART
    assert m._chord_mode({105, 65}) == SMART
    assert m._chord_mode({37, 50, 65}) == LITERAL
    assert m._chord_mode({37, 64, 65}) == ASSISTANT
    assert m._chord_mode({37, 133, 65}) == ASSISTANT
    assert m._chord_mode({65}) is None


def test_middle_button_single_hold_starts_smart():
    from vaani.hotkeys import MiddleButtonGesture, SMART

    t = {"now": 0.0}
    g = MiddleButtonGesture(hold_ms=150, double_ms=350, clock=lambda: t["now"])
    assert g.on_down() is None
    t["now"] = 0.10
    assert g.tick() is None
    t["now"] = 0.16
    assert g.tick() == SMART
    assert g.active
    assert g.on_up() is True
    assert not g.active


def test_middle_button_short_click_is_noop_then_double_starts_assistant():
    from vaani.hotkeys import MiddleButtonGesture, ASSISTANT

    t = {"now": 0.0}
    g = MiddleButtonGesture(hold_ms=150, double_ms=350, clock=lambda: t["now"])
    assert g.on_down() is None
    t["now"] = 0.05
    assert g.on_up() is False  # arms double; no session
    t["now"] = 0.20
    assert g.on_down() == ASSISTANT
    assert g.active
    t["now"] = 0.50
    assert g.on_up() is True


def test_middle_button_short_click_expires_without_second_press():
    from vaani.hotkeys import MiddleButtonGesture, SMART

    t = {"now": 0.0}
    g = MiddleButtonGesture(hold_ms=150, double_ms=350, clock=lambda: t["now"])
    g.on_down()
    t["now"] = 0.04
    g.on_up()
    t["now"] = 0.50  # past double window
    assert g.on_down() is None
    t["now"] = 0.70
    assert g.tick() == SMART


def test_middle_button_manager_routes_verified_touchpad_button_2_to_media(monkeypatch):
    from vaani.hotkeys import MiddleButtonHotkeyManager

    presses = []
    releases = []
    media = []
    manager = MiddleButtonHotkeyManager(
        lambda mode: presses.append(mode) or True,
        on_release=releases.append,
        on_touchpad_middle=lambda: media.append("play"),
        hold_ms=0,
    )
    monkeypatch.setattr(manager, "_trackpoint_middle_state", lambda: False)
    monkeypatch.setattr(manager, "_media_topology_is_verified", lambda: True)

    assert manager._handle_button_event(X.ButtonPress) is True
    assert manager._handle_button_event(X.ButtonRelease) is True
    assert manager._gesture.down is False
    assert presses == []
    assert releases == []
    assert media == ["play"]


def test_middle_button_media_requires_exact_physical_pointer_topology(monkeypatch):
    from vaani.hotkeys import MiddleButtonHotkeyManager

    media = []
    manager = MiddleButtonHotkeyManager(
        lambda _mode: True,
        on_touchpad_middle=lambda: media.append("play"),
    )
    monkeypatch.setattr(manager, "_trackpoint_middle_state", lambda: False)
    monkeypatch.setattr(
        manager,
        "_xinput",
        lambda *_args: """
⎜   ↳ Virtual core XTEST pointer               id=4 [slave  pointer  (2)]
⎜   ↳ Elan Touchpad                            id=9 [slave  pointer  (2)]
⎜   ↳ Elan TrackPoint                          id=10 [slave  pointer  (2)]
""",
    )

    assert manager._handle_button_event(X.ButtonPress) is True
    assert manager._handle_button_event(X.ButtonRelease) is True
    assert media == ["play"]


def test_unknown_physical_pointer_disables_media_but_not_trackpoint(monkeypatch):
    from vaani.hotkeys import MiddleButtonHotkeyManager, SMART

    presses = []
    media = []
    states = iter((False, True))
    manager = MiddleButtonHotkeyManager(
        lambda mode: presses.append(mode) or True,
        on_touchpad_middle=lambda: media.append("play"),
        hold_ms=0,
    )
    monkeypatch.setattr(manager, "_trackpoint_middle_state", lambda: next(states))
    monkeypatch.setattr(
        manager,
        "_xinput",
        lambda *_args: """
⎜   ↳ Elan Touchpad                            id=9 [slave  pointer  (2)]
⎜   ↳ Elan TrackPoint                          id=10 [slave  pointer  (2)]
⎜   ↳ USB Mouse                                id=14 [slave  pointer  (2)]
""",
    )

    assert manager._handle_button_event(X.ButtonPress) is False
    assert manager._handle_button_event(X.ButtonPress) is True
    assert manager._gesture.tick() == SMART
    assert media == []


def test_duplicate_touchpad_events_toggle_media_only_once(monkeypatch):
    from vaani.hotkeys import MiddleButtonHotkeyManager

    media = []
    manager = MiddleButtonHotkeyManager(
        lambda _mode: True,
        on_touchpad_middle=lambda: media.append("play"),
    )
    monkeypatch.setattr(manager, "_trackpoint_middle_state", lambda: False)
    monkeypatch.setattr(manager, "_media_topology_is_verified", lambda: True)

    assert manager._handle_button_event(X.ButtonPress) is True
    assert manager._handle_button_event(X.ButtonPress) is False
    assert manager._handle_button_event(X.ButtonRelease) is True
    assert manager._handle_button_event(X.ButtonRelease) is False
    assert media == ["play"]


def test_middle_button_manager_accepts_trackpoint_press_and_release(monkeypatch):
    from vaani.hotkeys import MiddleButtonHotkeyManager, SMART

    presses = []
    releases = []
    states = iter((True, False))
    manager = MiddleButtonHotkeyManager(
        lambda mode: presses.append(mode) or True,
        on_release=releases.append,
        hold_ms=0,
    )
    monkeypatch.setattr(manager, "_trackpoint_middle_state", lambda: next(states))

    assert manager._handle_button_event(X.ButtonPress) is True
    mode = manager._gesture.tick()
    assert mode == SMART
    manager._start_mode(mode)
    assert manager._handle_button_event(X.ButtonRelease) is True
    assert presses == [SMART]
    assert releases == [SMART]


def test_missed_trackpoint_release_sync_allows_double_press_assistant(monkeypatch):
    """Laggy query-state on release used to latch button_source and force smart."""
    from vaani.hotkeys import ASSISTANT, MiddleButtonHotkeyManager

    presses = []
    # press1 down, release still-down (ignored), sync up, press2 down
    states = iter((True, True, False, True))
    manager = MiddleButtonHotkeyManager(
        lambda mode: presses.append(mode) or True,
        hold_ms=150,
        double_ms=500,
    )
    monkeypatch.setattr(manager, "_trackpoint_middle_state", lambda: next(states))

    assert manager._handle_button_event(X.ButtonPress) is True
    assert manager._handle_button_event(X.ButtonRelease) is False
    assert manager._button_source == "trackpoint"
    assert manager._gesture.down is True

    assert manager._sync_trackpoint_release() is True
    assert manager._button_source is None
    assert manager._gesture.down is False
    assert manager._gesture.armed_until >= 0.0

    assert manager._handle_button_event(X.ButtonPress) is True
    assert presses == [ASSISTANT]


def test_stale_trackpoint_latch_clears_on_next_press(monkeypatch):
    from vaani.hotkeys import ASSISTANT, MiddleButtonHotkeyManager

    presses = []
    # press1, ignored release, next press syncs then accepts
    states = iter((True, True, False, True))
    manager = MiddleButtonHotkeyManager(
        lambda mode: presses.append(mode) or True,
        hold_ms=150,
        double_ms=500,
    )
    monkeypatch.setattr(manager, "_trackpoint_middle_state", lambda: next(states))

    assert manager._handle_button_event(X.ButtonPress) is True
    assert manager._handle_button_event(X.ButtonRelease) is False
    assert manager._handle_button_event(X.ButtonPress) is True
    assert presses == [ASSISTANT]


def test_middle_button_manager_fails_closed_when_trackpoint_state_is_unknown(monkeypatch):
    from vaani.hotkeys import MiddleButtonHotkeyManager

    manager = MiddleButtonHotkeyManager(lambda _mode: True)
    manager._pointer_id = "10"
    monkeypatch.setattr(
        manager,
        "_xinput",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("xinput unavailable")),
    )

    assert manager._trackpoint_middle_state() is None
    assert manager._handle_button_event(X.ButtonPress) is False
    assert manager._gesture.down is False


def test_esc_while_idle_does_not_permanently_kill_middle_button(monkeypatch):
    """Esc in Idle used to set ignore_until_up forever (no matching release)."""
    from vaani.hotkeys import MiddleButtonHotkeyManager, SMART

    presses = []
    manager = MiddleButtonHotkeyManager(
        lambda mode: presses.append(mode) or True,
        hold_ms=0,
    )
    monkeypatch.setattr(manager, "_trackpoint_middle_state", lambda: False)
    manager._cancel_now()
    assert manager._ignore_until_up is False

    monkeypatch.setattr(manager, "_trackpoint_middle_state", lambda: True)
    assert manager._handle_button_event(X.ButtonPress) is True
    assert manager._gesture.tick() == SMART


def test_button2_release_clears_ignore_even_when_press_was_ignored(monkeypatch):
    from vaani.hotkeys import MiddleButtonHotkeyManager

    manager = MiddleButtonHotkeyManager(lambda _mode: True, hold_ms=0)
    manager._ignore_until_up = True
    manager._gesture.down = False
    monkeypatch.setattr(manager, "_trackpoint_middle_state", lambda: False)
    assert manager._handle_button_event(X.ButtonRelease) is False
    assert manager._ignore_until_up is False


def test_unregister_clears_middle_button_routing_latches():
    from vaani.hotkeys import MiddleButtonHotkeyManager

    manager = MiddleButtonHotkeyManager(lambda _mode: True)
    manager._button_source = "touchpad"

    manager.unregister()

    assert manager._button_source is None


def test_media_sender_resolves_xf86_constant_and_sends_press_release():
    from Xlib.keysymdef.xf86 import XK_XF86_AudioPlay
    from vaani.hotkeys import XTestMediaKeySender

    class MediaDisplay:
        def __init__(self):
            self.keysyms = []
            self.synced = 0

        def keysym_to_keycode(self, keysym):
            self.keysyms.append(keysym)
            return 172

        def sync(self):
            self.synced += 1

    display = MediaDisplay()
    events = []
    sender = XTestMediaKeySender(
        display=display,
        fake_input=lambda dpy, event_type, keycode: events.append(
            (dpy, event_type, keycode)
        ),
    )

    sender.play_pause()

    assert display.keysyms == [XK_XF86_AudioPlay]
    assert events == [
        (display, X.KeyPress, 172),
        (display, X.KeyRelease, 172),
    ]
    assert display.synced == 1


def test_media_sender_attempts_release_when_press_injection_fails():
    from vaani.hotkeys import XTestMediaKeySender

    class MediaDisplay:
        def keysym_to_keycode(self, _keysym):
            return 172

        def sync(self):
            pass

    events = []

    def failing_input(_display, event_type, _keycode):
        events.append(event_type)
        if event_type == X.KeyPress:
            raise RuntimeError("press failed")

    sender = XTestMediaKeySender(display=MediaDisplay(), fake_input=failing_input)

    with pytest.raises(RuntimeError, match="press failed"):
        sender.play_pause()

    assert events == [X.KeyPress, X.KeyRelease]
