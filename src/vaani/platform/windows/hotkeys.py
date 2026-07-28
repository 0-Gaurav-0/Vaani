"""Global hotkeys for Windows via pynput (hold-to-talk press/release)."""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable


SMART = "smart"
LITERAL = "literal"
ASSISTANT = "assistant"

# Most-specific chords first so Ctrl+Shift+Space does not match Ctrl+Space.
_CHORD_SPECS: tuple[tuple[frozenset[str], str, str], ...] = (
    (frozenset({"ctrl", "shift", "space"}), LITERAL, "Ctrl+Shift+Space"),
    (frozenset({"ctrl", "alt", "space"}), ASSISTANT, "Ctrl+Alt+Space"),
    (frozenset({"ctrl", "space"}), SMART, "Ctrl+Space"),
)


def _normalize_key(key: Any) -> str | None:
    """Map a pynput key to a coarse token used for chord matching."""
    name = getattr(key, "name", None)
    if isinstance(name, str):
        lowered = name.lower()
        if lowered in {"ctrl", "ctrl_l", "ctrl_r"}:
            return "ctrl"
        if lowered in {"shift", "shift_l", "shift_r"}:
            return "shift"
        if lowered in {"alt", "alt_l", "alt_r", "alt_gr"}:
            return "alt"
        if lowered == "space":
            return "space"
        if lowered in {"esc", "escape"}:
            return "esc"
        return lowered
    char = getattr(key, "char", None)
    if char == " ":
        return "space"
    return None


class WindowsHotkeyService:
    """Ctrl+Space family hold-to-talk; Esc cancels.

    Press starts capture; release of the chord (typically Space) stops it.
    """

    def __init__(
        self,
        on_trigger: Callable[[str], None],
        *,
        on_release: Callable[[str], Any] | None = None,
        on_cancel: Callable[[], Any] | None = None,
        listener_factory: Callable[..., Any] | None = None,
        logger: logging.Logger | None = None,
    ):
        self.on_trigger = on_trigger
        self.on_release = on_release
        self.on_cancel = on_cancel
        self._listener_factory = listener_factory
        self._listener: Any | None = None
        self._lock = threading.Lock()
        self.logger = logger or logging.getLogger("vaani")
        self._pressed: set[str] = set()
        self._held_action: str | None = None
        self._press_count = 0
        self._release_count = 0
        self._registered = False

    def register(self) -> None:
        with self._lock:
            if self._registered or self._listener is not None:
                return
            if self._listener_factory is not None:
                self._register_test_factory()
                return
            self._register_pynput()
            self._registered = True

    def _register_test_factory(self) -> None:
        press_mapping: dict[str, Callable[[], None]] = {
            "<ctrl>+<space>": self._make_press(SMART),
            "<ctrl>+<shift>+<space>": self._make_press(LITERAL),
            "<ctrl>+<alt>+<space>": self._make_press(ASSISTANT),
        }
        release_mapping: dict[str, Callable[[], None]] = {
            "<ctrl>+<space>": self._make_release(SMART),
            "<ctrl>+<shift>+<space>": self._make_release(LITERAL),
            "<ctrl>+<alt>+<space>": self._make_release(ASSISTANT),
        }
        if self.on_cancel is not None:
            press_mapping["<esc>"] = self._make_cancel()
        factory = self._listener_factory
        try:
            listener = factory(press_mapping, release_mapping)
        except TypeError:
            listener = factory(press_mapping)
        start = getattr(listener, "start", None)
        if callable(start):
            start()
        self._listener = listener
        self._registered = True

    def _register_pynput(self) -> None:
        from pynput import keyboard

        service = self

        def on_press(key: Any) -> None:
            token = _normalize_key(key)
            if token is None:
                return
            if token == "esc":
                service.logger.info("event=hotkey_pressed action=cancel label=Esc")
                print("[vaani] hotkey pressed: Esc (cancel)", flush=True)
                if service.on_cancel is not None:
                    try:
                        service.on_cancel()
                    except Exception:
                        service.logger.exception("event=hotkey_cancel_error")
                return
            service._pressed.add(token)
            # Start only when Space is the key that completes the chord.
            # This prevents a stale/missed Space-release event from making a
            # later Ctrl press look like a new dictation request.
            if token != "space":
                return
            action = service._match_action()
            if action is None or service._held_action is not None:
                return
            label = next(
                (lbl for keys, act, lbl in _CHORD_SPECS if act == action), action
            )
            service._held_action = action
            service._press_count += 1
            service.logger.info(
                "event=hotkey_pressed action=%s label=%s count=%s",
                action,
                label,
                service._press_count,
            )
            print(f"[vaani] hotkey pressed: {label} ({action})", flush=True)
            try:
                service.on_trigger(action)
                service.logger.info(
                    "event=hotkey_trigger_dispatched action=%s", action
                )
            except Exception:
                service.logger.exception(
                    "event=hotkey_handler_error detail=press"
                )

        def on_release(key: Any) -> None:
            token = _normalize_key(key)
            if token is None:
                return
            service._pressed.discard(token)
            held = service._held_action
            if held is None:
                return
            # Chord broken (Space up, or a required modifier released).
            if service._match_action() == held:
                return
            service._held_action = None
            service._release_count += 1
            label = next(
                (lbl for keys, act, lbl in _CHORD_SPECS if act == held), held
            )
            service.logger.info(
                "event=hotkey_released action=%s label=%s count=%s",
                held,
                label,
                service._release_count,
            )
            print(f"[vaani] hotkey released: {label} ({held})", flush=True)
            if service.on_release is None:
                return
            try:
                service.on_release(held)
                service.logger.info(
                    "event=hotkey_release_dispatched action=%s", held
                )
            except Exception:
                service.logger.exception(
                    "event=hotkey_handler_error detail=release"
                )

        listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        listener.start()
        self._listener = listener
        self.logger.info(
            "event=hotkey_armed count=%s backend=pynput_listener",
            len(_CHORD_SPECS),
        )
        print(
            "Vaani hotkeys ready (hold-to-talk):\n"
            "  Hold Ctrl+Space           → smart dictation\n"
            "  Hold Ctrl+Shift+Space     → literal\n"
            "  Hold Ctrl+Alt+Space       → assistant\n"
            "  Esc                       → cancel\n"
            "Release the chord to stop — the pill switches to processing.",
            flush=True,
        )
        for _keys, _action, label in _CHORD_SPECS:
            print(f"[vaani] registered {label}", flush=True)

    def _match_action(self) -> str | None:
        pressed = self._pressed
        for keys, action, _label in _CHORD_SPECS:
            if keys <= pressed:
                # Reject if extra modifiers from a more-specific family are held
                # when matching the plain Ctrl+Space chord.
                if action == SMART and (
                    "shift" in pressed or "alt" in pressed
                ):
                    continue
                if action == LITERAL and "alt" in pressed:
                    continue
                if action == ASSISTANT and "shift" in pressed:
                    continue
                return action
        return None

    def unregister(self) -> None:
        with self._lock:
            listener = self._listener
            self._listener = None
            self._registered = False
            self._held_action = None
            self._pressed.clear()
        self.logger.info(
            "event=hotkey_unregister presses=%s releases=%s",
            self._press_count,
            self._release_count,
        )
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

    def _make_press(self, mode: str) -> Callable[[], None]:
        def _cb() -> None:
            self._held_action = mode
            self._press_count += 1
            try:
                self.on_trigger(mode)
            except Exception:
                pass

        return _cb

    def _make_release(self, mode: str) -> Callable[[], None]:
        def _cb() -> None:
            if self._held_action != mode:
                return
            self._held_action = None
            self._release_count += 1
            if self.on_release is None:
                return
            try:
                self.on_release(mode)
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


HotkeyManager = WindowsHotkeyService
