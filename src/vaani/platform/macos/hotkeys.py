"""Global hotkeys for macOS.

Uses Carbon ``RegisterEventHotKey`` (HIToolbox) instead of pynput.

Default chords avoid macOS collisions:
- Command+Space → Spotlight
- Control+Space → Input Sources
- Control+Shift+Space → often reserved / awkward in terminals

Vaani macOS defaults (Control+Option family):
- Control+Option+V       → smart dictation
- Control+Option+Shift+V → literal
- Control+Option+A       → assistant
- Esc                    → cancel
"""
from __future__ import annotations

import ctypes
import ctypes.util
import logging
import threading
from typing import Any, Callable

SMART = "smart"
LITERAL = "literal"
ASSISTANT = "assistant"

# HIToolbox virtual key codes (ANSI)
_KEY_A = 0
_KEY_V = 9
_KEY_ESCAPE = 53

# EventModifiers (Carbon)
_CMD = 1 << 8
_SHIFT = 1 << 9
_OPTION = 1 << 11
_CONTROL = 1 << 12

# (key_code, modifiers, mode_or_cancel)
_BINDINGS: tuple[tuple[int, int, str], ...] = (
    (_KEY_V, _CONTROL | _OPTION, SMART),
    (_KEY_V, _CONTROL | _OPTION | _SHIFT, LITERAL),
    (_KEY_A, _CONTROL | _OPTION, ASSISTANT),
    (_KEY_ESCAPE, 0, "cancel"),
)


def _fourcc(text: str) -> int:
    return (ord(text[0]) << 24) | (ord(text[1]) << 16) | (ord(text[2]) << 8) | ord(text[3])


class EventTypeSpec(ctypes.Structure):
    _fields_ = [("eventClass", ctypes.c_uint32), ("eventKind", ctypes.c_uint32)]


class EventHotKeyID(ctypes.Structure):
    _fields_ = [("signature", ctypes.c_uint32), ("id", ctypes.c_uint32)]


class HotkeyService:
    """Register smart/literal/assistant chords and Esc-to-cancel via Carbon."""

    def __init__(
        self,
        on_trigger: Callable[[str], None],
        *,
        on_cancel: Callable[[], Any] | None = None,
        listener_factory: Callable[..., Any] | None = None,
        logger: logging.Logger | None = None,
    ):
        self.on_trigger = on_trigger
        self.on_cancel = on_cancel
        # listener_factory kept for unit tests (fake pynput-style register path)
        self._listener_factory = listener_factory
        self._listener: Any | None = None
        self._lock = threading.Lock()
        self.logger = logger or logging.getLogger("vaani")
        self.trusted: bool | None = True
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._hotkey_refs: list[ctypes.c_void_p] = []
        self._handler_ref = ctypes.c_void_p()
        self._carbon: Any | None = None
        self._handler_proc: Any | None = None

    def register(self) -> None:
        with self._lock:
            if self._listener is not None or self._thread is not None:
                return
            if self._listener_factory is not None:
                self._register_test_factory()
                return
            self._register_carbon()

    def _register_test_factory(self) -> None:
        mapping: dict[str, Callable[[], None]] = {
            "<ctrl>+<alt>+v": self._make_trigger(SMART),
            "<ctrl>+<alt>+<shift>+v": self._make_trigger(LITERAL),
            "<ctrl>+<alt>+a": self._make_trigger(ASSISTANT),
        }
        if self.on_cancel is not None:
            mapping["<esc>"] = self._make_cancel()
        listener = self._listener_factory(mapping)
        listener.start()
        self._listener = listener

    def _register_carbon(self) -> None:
        lib_name = ctypes.util.find_library("Carbon")
        if not lib_name:
            raise RuntimeError("Carbon framework not found — cannot register macOS hotkeys")
        carbon = ctypes.cdll.LoadLibrary(lib_name)
        self._carbon = carbon

        # Prototypes
        carbon.GetEventDispatcherTarget.restype = ctypes.c_void_p
        carbon.RegisterEventHotKey.argtypes = [
            ctypes.c_uint32,
            ctypes.c_uint32,
            EventHotKeyID,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        carbon.RegisterEventHotKey.restype = ctypes.c_int32
        carbon.UnregisterEventHotKey.argtypes = [ctypes.c_void_p]
        carbon.UnregisterEventHotKey.restype = ctypes.c_int32
        carbon.InstallEventHandler.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(EventTypeSpec),
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        carbon.InstallEventHandler.restype = ctypes.c_int32
        carbon.RemoveEventHandler.argtypes = [ctypes.c_void_p]
        carbon.RemoveEventHandler.restype = ctypes.c_int32
        carbon.GetEventParameter.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.c_void_p,
        ]
        carbon.GetEventParameter.restype = ctypes.c_int32
        carbon.ReceiveNextEvent.argtypes = [
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_double,
            ctypes.c_bool,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        carbon.ReceiveNextEvent.restype = ctypes.c_int32
        carbon.SendEventToEventTarget.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        carbon.SendEventToEventTarget.restype = ctypes.c_int32
        carbon.ReleaseEvent.argtypes = [ctypes.c_void_p]
        carbon.ReleaseEvent.restype = ctypes.c_int32

        EventHandlerProc = ctypes.CFUNCTYPE(
            ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p
        )

        service = self

        @EventHandlerProc
        def _handler(_next_handler, event, _user_data):
            hotkey_id = EventHotKeyID()
            actual = ctypes.c_uint32(0)
            err = carbon.GetEventParameter(
                event,
                _fourcc("hkey"),  # kEventParamDirectObject
                _fourcc("hkid"),  # typeEventHotKeyID
                None,
                ctypes.sizeof(hotkey_id),
                ctypes.byref(actual),
                ctypes.byref(hotkey_id),
            )
            if err != 0:
                return 0
            idx = int(hotkey_id.id)
            if idx < 0 or idx >= len(_BINDINGS):
                return 0
            _key, _mods, action = _BINDINGS[idx]
            try:
                if action == "cancel":
                    if service.on_cancel is not None:
                        service.on_cancel()
                else:
                    service.on_trigger(action)
            except Exception:
                pass
            return 0

        # Keep callback alive for the process lifetime of the listener.
        self._handler_proc = _handler

        target = carbon.GetEventDispatcherTarget()
        if not target:
            raise RuntimeError("GetEventDispatcherTarget failed")

        spec = EventTypeSpec(eventClass=_fourcc("keyb"), eventKind=6)  # kEventHotKeyPressed
        err = carbon.InstallEventHandler(
            target,
            self._handler_proc,
            1,
            ctypes.byref(spec),
            None,
            ctypes.byref(self._handler_ref),
        )
        if err != 0:
            raise RuntimeError(f"InstallEventHandler failed ({err})")

        signature = _fourcc("vani")
        for index, (key_code, modifiers, action) in enumerate(_BINDINGS):
            hotkey_id = EventHotKeyID(signature=signature, id=index)
            ref = ctypes.c_void_p()
            status = carbon.RegisterEventHotKey(
                key_code,
                modifiers,
                hotkey_id,
                target,
                0,
                ctypes.byref(ref),
            )
            if status != 0:
                self.logger.error(
                    "RegisterEventHotKey failed for %s status=%s "
                    "(shortcut may be taken by macOS Input Sources or another app)",
                    action,
                    status,
                )
                continue
            self._hotkey_refs.append(ref)

        if not self._hotkey_refs:
            raise RuntimeError(
                "No macOS hotkeys registered — another app may own "
                "Control+Option+V / Control+Option+A. Quit conflicting shortcuts and retry."
            )

        self._stop.clear()
        self._thread = threading.Thread(target=self._run_loop, name="vaani-mac-hotkeys", daemon=True)
        self._thread.start()
        self.logger.info(
            "hotkeys armed (Carbon): Control+Option+V smart, "
            "Control+Option+Shift+V literal, Control+Option+A assistant, Esc cancel"
        )
        print(
            "Vaani hotkeys ready:\n"
            "  Control+Option+V       → smart dictation\n"
            "  Control+Option+Shift+V → literal\n"
            "  Control+Option+A       → assistant\n"
            "  Esc                    → cancel\n"
            "(Avoids Spotlight ⌘Space and Input Sources ⌃Space.)",
            flush=True,
        )

    def _run_loop(self) -> None:
        carbon = self._carbon
        if carbon is None:
            return
        event = ctypes.c_void_p()
        target = carbon.GetEventDispatcherTarget()
        # eventLoopTimedOutErr = -9875
        while not self._stop.is_set():
            err = carbon.ReceiveNextEvent(0, None, 0.25, True, ctypes.byref(event))
            if err == 0 and event:
                carbon.SendEventToEventTarget(event, target)
                carbon.ReleaseEvent(event)
                event = ctypes.c_void_p()
            elif err not in (0, -9875):
                # Unexpected error — keep looping unless stopping.
                if self._stop.is_set():
                    break

    def unregister(self) -> None:
        with self._lock:
            listener = self._listener
            self._listener = None
            thread = self._thread
            self._thread = None
            refs = list(self._hotkey_refs)
            self._hotkey_refs.clear()
            handler_ref = self._handler_ref
            self._handler_ref = ctypes.c_void_p()
            carbon = self._carbon
        if listener is not None:
            for method_name in ("stop", "join"):
                method = getattr(listener, method_name, None)
                if method is None:
                    continue
                try:
                    method()
                except Exception:
                    pass
            return
        self._stop.set()
        if carbon is not None:
            for ref in refs:
                try:
                    carbon.UnregisterEventHotKey(ref)
                except Exception:
                    pass
            if handler_ref:
                try:
                    carbon.RemoveEventHandler(handler_ref)
                except Exception:
                    pass
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)

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
