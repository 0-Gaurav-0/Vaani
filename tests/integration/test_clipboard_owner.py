from vaani.delivery import ClipboardDelivery, DeliveryStatus

class C:
    def set_text(self,t): self.t=t
    def read_text(self): return self.t
class T:
    def unchanged(self,s): return True
class P:
    def paste(self): pass

def test_owner_retained_and_shutdown_cancels():
    d=ClipboardDelivery(C(), T(), P()); owner=d._owner; assert d.deliver('line1\nदेवनागरी', object()) == DeliveryStatus.PASTE_DISPATCHED
    d.shutdown(); assert d._owner is None; assert d.deliver('late', object()) == DeliveryStatus.FAILED
