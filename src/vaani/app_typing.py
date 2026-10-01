""""open <app> and write <text>": launch, wait for *that* window, then paste.

Text is only pasted once the active X11 window belongs to the launched app
(WM_CLASS match). If it never gets focus we copy to the clipboard instead —
never type into whatever else happens to be focused.
"""
from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Callable

LOGGER = logging.getLogger("vaani")

_OPEN = r"(?:open|launch|start|kholo|khol\s*do|khol|open\s+karo|open\s+kar\s+do|chalu\s+karo)"
_WRITE = (
    r"(?:write|type|likho|likh\s*do|likh\s*de|likh|note\s+down|jot\s+down|put|paste|daal\s*do|daalo)"
)
_JOIN = r"(?:and|aur|then|phir|and\s+then|,)"
_INTO = r"(?:(?:in\s+it|into\s+it|there|usme|us\s+me|usmein|isme|is\s+me|ismein|mein|me)\s+)?"
_POLITE = r"(?:(?:please|pls|can\s+you|could\s+you|zara|jaldi)\s+)*"

# "open text editor and write buy milk" / "notepad kholo aur likho buy milk"
_VERB_FIRST = [
    re.compile(rf"^{_POLITE}{_OPEN}\s+(?P<app>.+?)\s*{_JOIN}\s+{_INTO}{_WRITE}\s*:?\s+(?P<text>.+)$", re.I),
    re.compile(rf"^{_POLITE}(?P<app>.+?)\s+{_OPEN}\s*{_JOIN}\s+{_INTO}{_WRITE}\s*:?\s+(?P<text>.+)$", re.I),
]
# "notepad kholo aur usme buy milk likh do"
_TEXT_FIRST = [
    re.compile(rf"^{_POLITE}{_OPEN}\s+(?P<app>.+?)\s*{_JOIN}\s+{_INTO}(?P<text>.+?)\s+{_WRITE}$", re.I),
    re.compile(rf"^{_POLITE}(?P<app>.+?)\s+{_OPEN}\s*{_JOIN}\s+{_INTO}(?P<text>.+?)\s+{_WRITE}$", re.I),
]
_QUOTES = "\"'“”‘’"


@dataclass(frozen=True)
class TypeRequest:
    app: str
    text: str


def parse_type_request(command: str) -> TypeRequest | None:
    raw = " ".join((command or "").strip().split())
    raw = raw.rstrip(".!?") if not raw.endswith("...") else raw
    for regex in (*_VERB_FIRST, *_TEXT_FIRST):
        m = regex.search(raw)
        if not m:
            continue
        app = m.group("app").strip(" ,")
        text = m.group("text").strip(" ,").strip(_QUOTES).strip()
        # "that ..." / "ki ..." introducers carry no content.
        text = re.sub(r"^(?:that|ki|yeh|ye)\s+", "", text, flags=re.I)
        if app and text:
            return TypeRequest(app, text)
    return None


# Executable → WM_CLASS fragments seen on this desktop (lowercase).
_CLASS_HINTS = {
    "gnome-text-editor": ("gedit", "text-editor", "texteditor"),
    "gedit": ("gedit",),
    "code": ("code",),
    "cursor": ("cursor",),
    "libreoffice": ("libreoffice", "soffice"),
    "obsidian": ("obsidian",),
    "gnome-terminal": ("gnome-terminal",),
    "slack": ("slack",),
    "thunderbird": ("thunderbird",),
    "antigravity-ide": ("antigravity",),
    "zed": ("zed",),
}


def class_hints(executable: str, app_name: str) -> tuple[str, ...]:
    base = os.path.basename(executable or "").casefold()
    hints = list(_CLASS_HINTS.get(base, (base,)))
    generic = {"text", "editor", "code", "app", "apps", "desktop", "viewer", "manager", "office"}
    hints += [
        w for w in re.findall(r"[a-z0-9]+", (app_name or "").casefold()) if len(w) > 3 and w not in generic
    ]
    return tuple(dict.fromkeys(h for h in hints if h))


class WindowWatcher:
    """Minimal X11 reads: active window id + its WM_CLASS."""

    def __init__(self, display: Any | None = None):
        if display is None:
            from Xlib.display import Display

            display = Display()
        self.display = display
        self._root = display.screen().root
        self._atom = display.intern_atom("_NET_ACTIVE_WINDOW")

    def active(self) -> tuple[int | None, str]:
        try:
            prop = self._root.get_full_property(self._atom, 0)
            wid = int(prop.value[0]) if prop is not None and prop.value else None
            if not wid:
                return None, ""
            win = self.display.create_resource_object("window", wid)
            cls = win.get_wm_class() or ()
            return wid, " ".join(str(c) for c in cls).casefold()
        except Exception:
            return None, ""

    def close(self) -> None:
        try:
            self.display.close()
        except Exception:
            pass


def wait_for_app_window(
    watcher: Any,
    hints: tuple[str, ...],
    *,
    before: int | None,
    timeout: float = 8.0,
    settle: float = 0.45,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """True once the active window belongs to the launched app (then settle)."""
    deadline = clock() + timeout
    while clock() < deadline:
        wid, cls = watcher.active()
        if wid and cls and any(h in cls for h in hints):
            # A brand-new window or an already-open one brought forward both count;
            # settle so the editor finishes creating its text view.
            sleep(settle)
            wid2, cls2 = watcher.active()
            if wid2 and any(h in cls2 for h in hints):
                return True
        sleep(0.1)
    return False
