from vaani.delivery import DeliveryStatus
from vaani.platform.linux.wayland.delivery import WaylandClipboardDelivery

class Clip:
    def __init__(self, value=None): self.value = value
    def set_text(self, text): self.value = text
    def read_text(self): return self.value

class Paste:
    def __init__(self, fail=False): self.calls, self.fail = 0, fail
    def paste(self):
        self.calls += 1
        if self.fail: raise RuntimeError("wtype unavailable")

def test_paste_dispatched_when_wtype_succeeds():
    p = Paste()
    assert WaylandClipboardDelivery(Clip(), p).deliver("hello") == DeliveryStatus.PASTE_DISPATCHED
    assert p.calls == 1

def test_clipboard_only_when_paste_fails():
    c = Clip()
    assert WaylandClipboardDelivery(c, Paste(fail=True)).deliver("hello") == DeliveryStatus.CLIPBOARD_ONLY
    assert c.value == "hello"

def test_failed_when_clipboard_set_raises():
    class BadClip(Clip):
        def set_text(self, text): raise RuntimeError("wl-copy missing")
    assert WaylandClipboardDelivery(BadClip(), Paste()).deliver("hello") == DeliveryStatus.FAILED

def test_failed_when_readback_never_matches():
    class StaleClip(Clip):
        def read_text(self): return "stale"
    assert WaylandClipboardDelivery(StaleClip(), Paste(), readback_timeout=0.05).deliver("hello") == DeliveryStatus.FAILED

def test_cancel_prevents_delivery():
    d = WaylandClipboardDelivery(Clip(), Paste())
    d.cancel()
    assert d.deliver("hello") == DeliveryStatus.FAILED

def test_empty_text_is_failed():
    assert WaylandClipboardDelivery(Clip(), Paste()).deliver("") == DeliveryStatus.FAILED
