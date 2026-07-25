"""Small, best-effort X11 target probes used to protect delivery."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True)
class TargetSnapshot:
    active_window: int
    input_focus: int

class X11Probe:
    def __init__(self, display: Any | None = None):
        self.display = display
        self._root = None
        if display is not None:
            self._root = display.screen().root

    def _property(self, name: str) -> int | None:
        try:
            atom = self.display.intern_atom(name)
            getter = getattr(self._root, "get_full_property", None)
            value = getter(atom, 0) if getter else self._root.get_property(atom, 0, 0, 1)
            if value is None or not getattr(value, "value", None):
                return None
            return int(value.value[0])
        except Exception:
            return None

    def active_window(self) -> int | None:
        return self._property("_NET_ACTIVE_WINDOW")

    def input_focus(self) -> int | None:
        try:
            result = self.display.get_input_focus()
            focus = getattr(result, "focus", result)
            return int(getattr(focus, "id", focus))
        except Exception:
            return None

    def snapshot(self) -> TargetSnapshot | None:
        active, focus = self.active_window(), self.input_focus()
        if active is None or focus is None:
            return None
        return TargetSnapshot(active, focus)

    def unchanged(self, before: TargetSnapshot) -> bool:
        current = self.snapshot()
        return current is not None and current == before

    # Friendly adapter names used by controller/delivery integrations.
    capture = snapshot
    probe = snapshot

    def close(self) -> None:
        if self.display is not None:
            try: self.display.close()
            except Exception: pass

def open_probe() -> X11Probe:
    from Xlib.display import Display
    return X11Probe(Display())

TargetProbe = X11Probe

def probe_target(display: Any | None = None) -> TargetSnapshot | None:
    return X11Probe(display or __import__("Xlib.display", fromlist=["Display"]).Display()).snapshot()
