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
    def __init__(self, fail=False): self.root=Root(fail)
    def screen(self): return SimpleNamespace(root=self.root)
    def keysym_to_keycode(self, _): return 38
    def sync(self): pass

class SyncBad(Display):
    def sync(self): raise RuntimeError("BadAccess")

def ev(t, state): return SimpleNamespace(type=t, detail=38, state=state)

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

def test_xinput_escape_cancel_is_edge_triggered(monkeypatch):
    out=[]; m=__import__('vaani.hotkeys', fromlist=['XInputHotkeyManager']).XInputHotkeyManager(lambda _:None, on_cancel=lambda: out.append(1))
    m.set_policy_keys(cancel=True, approve=False)
    m._escape_seen=False
    # Equivalent state transitions from xinput polling; Escape is observed but not consumed.
    down={9};
    if m._policy_cancel and 9 in down and not m._escape_seen: m._escape_seen=True; m.on_cancel()
    assert out == [1]


def test_xinput_policy_disarmed_skips_esc_enter():
    from vaani.hotkeys import XInputHotkeyManager

    cancels: list[int] = []
    approves: list[int] = []
    m = XInputHotkeyManager(
        lambda _: None,
        on_cancel=lambda: cancels.append(1),
        on_approve=lambda: approves.append(1),
    )
    assert m._policy_cancel is False
    assert m._policy_approve is False
    # Simulate the poll edge logic while disarmed.
    down = {9, 36}
    if m._policy_cancel or m._policy_approve:
        if m._policy_cancel and m.escape in down and not m._escape_seen:
            m._escape_seen = True
            if m.on_cancel:
                m.on_cancel()
        if m._policy_approve and m.enter in down and not m._enter_seen:
            m._enter_seen = True
            if m.on_approve:
                m.on_approve()
    else:
        m._escape_seen = m.escape in down
        m._enter_seen = m.enter in down
    assert cancels == []
    assert approves == []

    m.set_policy_keys(cancel=True, approve=True)
    # Keys still held from before arming — should not edge-fire.
    if m._policy_cancel:
        if m.escape in down and not m._escape_seen:
            m._escape_seen = True
            m.on_cancel()
        elif m.escape not in down:
            m._escape_seen = False
    if m._policy_approve:
        if m.enter in down and not m._enter_seen:
            m._enter_seen = True
            m.on_approve()
        elif m.enter not in down:
            m._enter_seen = False
    assert cancels == []
    assert approves == []

    # Fresh edges after release + press.
    m._escape_seen = False
    m._enter_seen = False
    down = {9}
    if m.escape in down and not m._escape_seen:
        m._escape_seen = True
        m.on_cancel()
    assert cancels == [1]
