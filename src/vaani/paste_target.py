"""Decide Ctrl+V vs Ctrl+Shift+V for paste."""
from __future__ import annotations

from typing import Any, Callable

TERMINAL_CLASS_MARKERS = (
    "terminal",
    "gnome-terminal",
    "konsole",
    "alacritty",
    "kitty",
    "xterm",
    "tilix",
    "terminator",
    "wezterm",
    "ghostty",
    "ptyxis",
)

IDE_CLASS_MARKERS = (
    "cursor",
    "code",
    "code - oss",
    "vscodium",
    "jetbrains-",
)

TERMINAL_A11Y_MARKERS = ("terminal", "vte", "console")


def normalize_wm_class(classes: Any) -> str:
    if not classes:
        return ""
    if isinstance(classes, str):
        return classes.casefold()
    try:
        return " ".join(str(part) for part in classes).casefold()
    except Exception:
        return str(classes).casefold()


def is_terminal_wm_class(classes: str) -> bool:
    blob = classes.casefold()
    return any(marker in blob for marker in TERMINAL_CLASS_MARKERS)


def is_ide_wm_class(classes: str) -> bool:
    blob = classes.casefold()
    return any(marker in blob for marker in IDE_CLASS_MARKERS)


def active_wm_class(display: Any) -> str:
    try:
        from Xlib import X

        root = display.screen().root
        atom = display.intern_atom("_NET_ACTIVE_WINDOW")
        prop = root.get_full_property(atom, X.AnyPropertyType)
        wid = int(prop.value[0]) if prop is not None and getattr(prop, "value", None) else 0
        if not wid:
            return ""
        window = display.create_resource_object("window", wid)
        return normalize_wm_class(window.get_wm_class())
    except Exception:
        return ""


def focused_is_terminal_a11y(*, focus_provider: Callable[[], Any] | None = None) -> bool:
    try:
        if focus_provider is not None:
            focused = focus_provider()
        else:
            import gi

            gi.require_version("Atspi", "2.0")
            from gi.repository import Atspi

            Atspi.init()
            focused = Atspi.get_focus() if hasattr(Atspi, "get_focus") else None
        if focused is None:
            return False
        parts: list[str] = []
        for attr in ("get_role_name", "get_name", "get_description"):
            getter = getattr(focused, attr, None)
            if callable(getter):
                try:
                    value = getter()
                    if value:
                        parts.append(str(value))
                except Exception:
                    pass
        blob = " ".join(parts).casefold()
        return any(marker in blob for marker in TERMINAL_A11Y_MARKERS)
    except Exception:
        return False


def needs_shift_paste(
    display: Any | None = None,
    *,
    wm_class: str | None = None,
    a11y_terminal: bool | None = None,
    focus_provider: Callable[[], Any] | None = None,
) -> bool:
    classes = wm_class if wm_class is not None else (
        active_wm_class(display) if display is not None else ""
    )
    if is_terminal_wm_class(classes):
        return True
    if not is_ide_wm_class(classes):
        return False
    if a11y_terminal is not None:
        return bool(a11y_terminal)
    return focused_is_terminal_a11y(focus_provider=focus_provider)
