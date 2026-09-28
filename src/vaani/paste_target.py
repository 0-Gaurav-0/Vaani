"""Decide Ctrl+V vs Ctrl+Shift+V for paste."""
from __future__ import annotations

import logging
import re
from typing import Any, Callable

logger = logging.getLogger("vaani.paste")

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
    "warp",  # WM_CLASS: dev.warp.Warp
    "foot",
    "rio",
    "hyper",
    "tabby",
    "contour",
    "guake",
    "xfce4-terminal",
    "mate-terminal",
    "cool-retro-term",
)

IDE_CLASS_MARKERS = (
    "cursor",
    "code",
    "code - oss",
    "vscodium",
    "jetbrains-",
)

TERMINAL_A11Y_MARKERS = (
    "terminal",
    "vte",
    "console",
    "xterm",
    "shell",
)

# IDE window titles when the integrated terminal (not a file) is focused.
_IDE_TERMINAL_TITLE_RE = re.compile(
    r"(?i)(?:^|[\s|—\-|:\[\]])(terminal|bash|zsh|fish|powershell|pwsh|cmd)(?:$|[\s|—\-|:\]])"
)


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


def active_window_title(display: Any) -> str:
    try:
        from Xlib import X

        root = display.screen().root
        atom = display.intern_atom("_NET_ACTIVE_WINDOW")
        prop = root.get_full_property(atom, X.AnyPropertyType)
        wid = int(prop.value[0]) if prop is not None and getattr(prop, "value", None) else 0
        if not wid:
            return ""
        window = display.create_resource_object("window", wid)
        net_name = display.intern_atom("_NET_WM_NAME")
        utf8 = display.intern_atom("UTF8_STRING")
        title_prop = window.get_full_property(net_name, utf8)
        if title_prop is not None and getattr(title_prop, "value", None):
            raw = title_prop.value
            if isinstance(raw, bytes):
                return raw.decode("utf-8", "replace")
            return str(raw)
        name = window.get_wm_name()
        return str(name) if name else ""
    except Exception:
        return ""


def title_suggests_ide_terminal(title: str) -> bool:
    if not title:
        return False
    return bool(_IDE_TERMINAL_TITLE_RE.search(title))


def _a11y_blob(node: Any) -> str:
    parts: list[str] = []
    for attr in ("get_role_name", "get_name", "get_description"):
        getter = getattr(node, attr, None)
        if not callable(getter):
            continue
        try:
            value = getter()
            if value:
                parts.append(str(value))
        except Exception:
            pass
    try:
        attrs = node.get_attributes()
        if attrs:
            items = dict(attrs)
            for key, value in items.items():
                joined = f"{key} {value}".casefold()
                if any(m in joined for m in TERMINAL_A11Y_MARKERS):
                    parts.append(joined)
    except Exception:
        pass
    return " ".join(parts).casefold()


def _blob_is_terminal(blob: str) -> bool:
    return any(marker in blob for marker in TERMINAL_A11Y_MARKERS)


def _node_is_focused(node: Any) -> bool:
    try:
        import gi

        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi

        state = node.get_state_set()
        return bool(state and state.contains(Atspi.StateType.FOCUSED))
    except Exception:
        return False


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

        # Focused node + ancestors (VS Code/Cursor often label a parent "Terminal").
        node = focused
        for _ in range(10):
            if node is None:
                break
            if _blob_is_terminal(_a11y_blob(node)):
                return True
            try:
                parent = node.get_parent()
            except Exception:
                parent = None
            if parent is None or parent is node:
                break
            node = parent

        # Fallback: find a FOCUSED terminal-like node under Cursor/Code.
        import gi

        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi

        Atspi.init()
        desktop = Atspi.get_desktop(0)
        for i in range(desktop.get_child_count()):
            app = desktop.get_child_at_index(i)
            try:
                app_name = (app.get_name() or "").casefold()
            except Exception:
                continue
            if not any(m in app_name for m in ("cursor", "code", "vscodium")):
                continue
            stack = [(app, 0)]
            seen = 0
            while stack and seen < 5000:
                cur, depth = stack.pop()
                seen += 1
                try:
                    blob = _a11y_blob(cur)
                except Exception:
                    blob = ""
                if blob and _blob_is_terminal(blob) and _node_is_focused(cur):
                    return True
                if depth >= 12:
                    continue
                try:
                    count = cur.get_child_count()
                except Exception:
                    count = 0
                for j in range(count):
                    try:
                        child = cur.get_child_at_index(j)
                    except Exception:
                        child = None
                    if child is not None:
                        stack.append((child, depth + 1))
        return False
    except Exception:
        return False


def needs_shift_paste(
    display: Any | None = None,
    *,
    wm_class: str | None = None,
    window_title: str | None = None,
    a11y_terminal: bool | None = None,
    focus_provider: Callable[[], Any] | None = None,
) -> bool:
    classes = wm_class if wm_class is not None else (
        active_wm_class(display) if display is not None else ""
    )
    if is_terminal_wm_class(classes):
        logger.info(
            "event=paste_keys shift=True reason=terminal_wm_class class=%r",
            classes,
        )
        return True
    if not is_ide_wm_class(classes):
        logger.info(
            "event=paste_keys shift=False reason=non_terminal class=%r",
            classes,
        )
        return False

    title = window_title if window_title is not None else (
        active_window_title(display) if display is not None else ""
    )
    if title_suggests_ide_terminal(title):
        logger.info(
            "event=paste_keys shift=True reason=ide_title class=%r title=%r",
            classes,
            (title[:80] + "…") if len(title) > 80 else title,
        )
        return True

    if a11y_terminal is not None:
        shift = bool(a11y_terminal)
        logger.info(
            "event=paste_keys shift=%s reason=ide_a11y_override class=%r",
            shift,
            classes,
        )
        return shift

    shift = focused_is_terminal_a11y(focus_provider=focus_provider)
    logger.info(
        "event=paste_keys shift=%s reason=ide_a11y class=%r title=%r",
        shift,
        classes,
        (title[:80] + "…") if len(title) > 80 else title,
    )
    return shift
