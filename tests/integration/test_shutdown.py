import threading
from pathlib import Path
from types import SimpleNamespace
from vaani.controller import Controller

class R:
    def start(self): return SimpleNamespace(path=Path('/tmp/a'), duration_seconds=1)
    def stop(self): return SimpleNamespace(path=Path('/tmp/a'), duration_seconds=1)
    def cleanup(self): pass
class Blocked:
    def __init__(self): self.enter=threading.Event()
    def transcribe(self,*a,**k): self.enter.set(); threading.Event().wait(10)
class D:
    def deliver(self,*a): return 'failed'
    def cancel(self): pass
class H:
    def insert(self,**k): raise AssertionError('late history')

def test_shutdown_bounded_with_unresponsive_worker():
    g=Blocked(); c=Controller(recorder=R(), groq=g, delivery=D(), history=H(), key_provider=lambda:'k', join_timeout=.01)
    c.trigger(); c.stop(); assert g.enter.wait(1)
    c.shutdown()
    assert c._shutdown
