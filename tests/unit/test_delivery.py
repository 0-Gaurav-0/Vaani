from vaani.delivery import ClipboardDelivery, DeliveryStatus

class Clip:
    def __init__(self, value=None): self.value=value
    def set_text(self, text): self.value=text
    def read_text(self): return self.value
class Target:
    def __init__(self, ok=True): self.ok=ok
    def unchanged(self, snap): return self.ok
class Paste:
    def __init__(self): self.calls=0
    def paste(self): self.calls+=1

def test_unicode_multiline_paste():
    p=Paste(); assert ClipboardDelivery(Clip(), Target(), p).deliver('नमस्ते\nhello', object()) == DeliveryStatus.PASTE_DISPATCHED; assert p.calls == 1
def test_target_change_keeps_verified_clipboard():
    c=Clip(); assert ClipboardDelivery(c, Target(False), Paste()).deliver('secret', object()) == DeliveryStatus.CLIPBOARD_ONLY; assert c.value == 'secret'
def test_missing_target_is_clipboard_only():
    assert ClipboardDelivery(Clip(), None, Paste()).deliver('x', object()) == DeliveryStatus.CLIPBOARD_ONLY
def test_cancel_prevents_delivery():
    c=Clip(); d=ClipboardDelivery(c, Target(), Paste()); d.cancel(); assert d.deliver('x', object()) == DeliveryStatus.FAILED
