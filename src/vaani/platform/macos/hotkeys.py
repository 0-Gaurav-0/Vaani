"""Global hotkeys for macOS via pynput GlobalHotKeys."""
from __future__ import annotations

import threading
from typing import Any, Callable

SMART = "smart"
LITERAL = "literal"
ASSISTANT = "assistant"

# pynput chord strings (Ctrl family matches Linux Vaani defaults).
_HOTKEY_MAP = {
    "<ctrl>+<space>": SMART,
    "<ctrl>+<shift>+<space>": LITERAL,
    "<ctrl>+<alt>+<space>": ASSISTANT,
}


class HotkeyService:
    """Register smart/literal/assistant chords and Esc-to-cancel in a background listener."""

    def __init__(
        self,
        on_trigger: Callable[[str], None],
        *,
        on_cancel: Callable[[], Any] | None = None,
        listener_factory: Callable[..., Any] | None = None,
    ):
        self.on_trigger = on_trigger
        self.on_cancel = on_cancel
        self._listener_factory = listener_factory
        self._listener: Any | None = None
        self._lock = threading.Lock()

    def register(self) -> None:
        with self._lock:
            if self._listener is not None:
                return
            mapping: dict[str, Callable[[], None]] = {}
            for chord, mode in _HOTKEY_MAP.items():
                mapping[chord] = self._make_trigger(mode)
            if self.on_cancel is not None:
                mapping["<esc>"] = self._make_cancel()

            factory = self._listener_factory
            if factory is None:
                from pynput.keyboard import GlobalHotKeys

                factory = GlobalHotKeys

            listener = factory(mapping)
            start = getattr(listener, "start", None)
            if start is None:
                raise RuntimeError("hotkey listener has no start()")
            start()
            self._listener = listener

    def unregister(self) -> None:
        with self._lock:
            listener = self._listener
            self._listener = None
        if listener is None:
            return
        for method_name in ("stop", "join"):
            method = getattr(listener, method_name, None)
            if method is None:
                continue
            try:
                method()
            except Exception:
                pass

    def _make_trigger(self, mode: str) -> Callable[[], None]:
        def _cb() -> None:
            try:
                self.on_trigger(mode)
            except Exception:
                pass

        return _cb

    def _make_cancel(self) -> Callable[[], None]:
        def _cb() -> None:
            if self.on_cancel is None:
                return
            try:
                self.on_cancel()
            except Exception:
                pass

        return _cb


HotkeyManager = HotkeyService
