from types import SimpleNamespace
from vaani.x11 import X11Probe, TargetSnapshot

class Root:
    def __init__(self, active=10): self.active=active
    def get_full_property(self, atom, _): return None if self.active is None else SimpleNamespace(value=[self.active])
class D:
    def __init__(self, active=10, focus=20): self.root=Root(active); self.focus=focus
    def screen(self): return SimpleNamespace(root=self.root)
    def intern_atom(self,n): return n
    def get_input_focus(self): return SimpleNamespace(focus=SimpleNamespace(id=self.focus))

def test_snapshot_and_change_detection():
    d=D(); p=X11Probe(d); s=p.snapshot(); assert s==TargetSnapshot(10,20); assert p.unchanged(s)
    d.focus=21; assert not p.unchanged(s)

def test_failures_return_none():
    assert X11Probe(D(active=None)).snapshot() is None
    class Bad(D):
        def get_input_focus(self): raise RuntimeError
    assert X11Probe(Bad()).snapshot() is None
