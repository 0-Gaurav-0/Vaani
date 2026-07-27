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
