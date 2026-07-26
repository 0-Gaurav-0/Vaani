"""Global hotkeys for Windows via pynput (RegisterHotKey fallback available)."""
from __future__ import annotations

from typing import Any, Callable


SMART = "smart"
LITERAL = "literal"
ASSISTANT = "assistant"


class WindowsHotkeyService:
    """Ctrl+Space smart, Ctrl+Shift+Space literal, Ctrl+Alt+Space assistant, Esc cancel."""

    def __init__(
        self,
        on_trigger: Callable[[str], None],
        *,
        on_cancel: Callable[[], None] | None = None,
        listener_factory: Callable[[dict[str, Callable[[], None]]], Any] | None = None,
    ):
        self.on_trigger = on_trigger
        self.on_cancel = on_cancel
        self._listener_factory = listener_factory
        self._listener: Any | None = None

    def _default_factory(self, mapping: dict[str, Callable[[], None]]) -> Any:
        from pynput.keyboard import GlobalHotKeys

        return GlobalHotKeys(mapping)

    def register(self) -> None:
        if self._listener is not None:
            return

        def fire(mode: str) -> None:
            try:
                self.on_trigger(mode)
            except Exception:
                pass

        def cancel() -> None:
            if self.on_cancel is None:
                return
            try:
                self.on_cancel()
            except Exception:
                pass

        mapping = {
            "<ctrl>+<space>": lambda: fire(SMART),
            "<ctrl>+<shift>+<space>": lambda: fire(LITERAL),
            "<ctrl>+<alt>+<space>": lambda: fire(ASSISTANT),
            "<esc>": cancel,
        }
        factory = self._listener_factory or self._default_factory
        self._listener = factory(mapping)
        start = getattr(self._listener, "start", None)
        if callable(start):
            start()

    def unregister(self) -> None:
        listener = self._listener
        self._listener = None
        if listener is None:
            return
        stop = getattr(listener, "stop", None)
        if callable(stop):
            try:
                stop()
            except Exception:
                pass
