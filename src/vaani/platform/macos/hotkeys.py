"""Global hotkeys for macOS via Carbon RegisterEventHotKey.

The Carbon event loop is pumped on the **main thread** (see ``pump``).
Background-thread ReceiveNextEvent often never delivers hotkey presses.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import logging
import sys
import threading
from typing import Any, Callable

SMART = "smart"
LITERAL = "literal"
ASSISTANT = "assistant"

_KEY_SPACE = 49  # kVK_Space
_KEY_ESCAPE = 53
_KEY_RETURN = 36  # kVK_Return — approve while PendingAction is staged

_CMD = 1 << 8
_SHIFT = 1 << 9
_OPTION = 1 << 11
_CONTROL = 1 << 12

# Hold-to-talk family (2-key smart chord; variants add Shift / Control).
# Esc rejects a pending confirm (same cancel family); Return approves.
_BINDINGS: tuple[tuple[int, int, str, str], ...] = (
    (_KEY_SPACE, _OPTION, SMART, "Option+Space"),
    (_KEY_SPACE, _OPTION | _SHIFT, LITERAL, "Option+Shift+Space"),
    (_KEY_SPACE, _OPTION | _CONTROL, ASSISTANT, "Control+Option+Space"),
    (_KEY_ESCAPE, 0, "cancel", "Esc"),
    (_KEY_RETURN, 0, "approve", "Enter"),
)

_EVENT_LOOP_TIMED_OUT = -9875
_EVENT_NOT_HANDLED = -9874


def _fourcc(text: str) -> int:
    value = 0
    for ch in text[:4].ljust(4, "\0"):
        value = (value << 8) | ord(ch)
    return value


# CarbonEvents.h
_K_EVENT_CLASS_KEYBOARD = _fourcc("keyb")
_K_EVENT_HOT_KEY_PRESSED = 5
_K_EVENT_HOT_KEY_RELEASED = 6
_K_EVENT_PARAM_DIRECT_OBJECT = _fourcc("----")  # kEventParamDirectObject
_TYPE_EVENT_HOT_KEY_ID = _fourcc("hkid")


class EventTypeSpec(ctypes.Structure):
    _fields_ = [("eventClass", ctypes.c_uint32), ("eventKind", ctypes.c_uint32)]


class EventHotKeyID(ctypes.Structure):
    _fields_ = [("signature", ctypes.c_uint32), ("id", ctypes.c_uint32)]


class HotkeyService:
    """Register chords; call ``pump()`` on the main thread while Vaani runs."""

    def __init__(
        self,
        on_trigger: Callable[[str], None],
        *,
        on_release: Callable[[str], Any] | None = None,
        on_cancel: Callable[[], Any] | None = None,
        on_approve: Callable[[], Any] | None = None,
        listener_factory: Callable[..., Any] | None = None,
        logger: logging.Logger | None = None,
    ):
        self.on_trigger = on_trigger
        self.on_release = on_release
        self.on_cancel = on_cancel
        self.on_approve = on_approve
        self._listener_factory = listener_factory
        self._listener: Any | None = None
        self._lock = threading.Lock()
        self.logger = logger or logging.getLogger("vaani")
        self.trusted: bool | None = True
        self._carbon: Any | None = None
        self._handler_proc: Any | None = None
        self._handler_ref = ctypes.c_void_p()
        self._hotkey_refs: list[ctypes.c_void_p] = []
        self._app_target: int | None = None
        self._dispatcher_target: int | None = None
        self._registered = False
        self._press_count = 0
        self._release_count = 0
        self._pump_count = 0
        self._last_loop_error: int | None = None
        self._held_action: str | None = None

    def register(self) -> None:
        with self._lock:
            if self._registered or self._listener is not None:
                return
            if self._listener_factory is not None:
                self._register_test_factory()
                return
            self._register_carbon()
            self._registered = True

    def _register_test_factory(self) -> None:
        mapping: dict[str, Callable[[], None]] = {
            "<alt>+<space>": self._make_trigger(SMART),
            "<alt>+<shift>+<space>": self._make_trigger(LITERAL),
            "<ctrl>+<alt>+<space>": self._make_trigger(ASSISTANT),
        }
        if self.on_cancel is not None:
            mapping["<esc>"] = self._make_cancel()
        if self.on_approve is not None:
            mapping["<enter>"] = self._make_approve()
            mapping["<return>"] = self._make_approve()
        listener = self._listener_factory(mapping)
        listener.start()
        self._listener = listener
        self._registered = True

    def _register_carbon(self) -> None:
        lib_name = ctypes.util.find_library("Carbon")
        self.logger.info("event=hotkey_carbon_load library=%s", lib_name)
        if not lib_name:
            raise RuntimeError("Carbon framework not found — cannot register macOS hotkeys")
        carbon = ctypes.cdll.LoadLibrary(lib_name)
        self._carbon = carbon

        carbon.GetEventDispatcherTarget.restype = ctypes.c_void_p
        carbon.GetApplicationEventTarget.restype = ctypes.c_void_p
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
        carbon.GetEventKind.argtypes = [ctypes.c_void_p]
        carbon.GetEventKind.restype = ctypes.c_uint32

        EventHandlerProc = ctypes.CFUNCTYPE(
            ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p
        )
        service = self

        @EventHandlerProc
        def _handler(_next_handler, event, _user_data):
            kind = int(carbon.GetEventKind(event))
            hotkey_id = EventHotKeyID()
            actual = ctypes.c_uint32(0)
            err = carbon.GetEventParameter(
                event,
                _K_EVENT_PARAM_DIRECT_OBJECT,
                _TYPE_EVENT_HOT_KEY_ID,
                None,
                ctypes.sizeof(hotkey_id),
                ctypes.byref(actual),
                ctypes.byref(hotkey_id),
            )
            if err != 0:
                service.logger.warning(
                    "event=hotkey_param_error status=%s (expected kEventParamDirectObject)",
                    err,
                )
                return 0
            idx = int(hotkey_id.id)
            if idx < 0 or idx >= len(_BINDINGS):
                service.logger.warning("event=hotkey_unknown_id id=%s", idx)
                return 0
            _key, _mods, action, label = _BINDINGS[idx]
            try:
                if kind == _K_EVENT_HOT_KEY_RELEASED:
                    if action in {"cancel", "approve"}:
                        return 0
                    if service._held_action != action:
                        return 0
                    service._held_action = None
                    service._release_count += 1
                    service.logger.info(
                        "event=hotkey_released action=%s label=%s count=%s",
                        action,
                        label,
                        service._release_count,
                    )
                    print(f"[vaani] hotkey released: {label} ({action})", flush=True)
                    if service.on_release is not None:
                        service.on_release(action)
                        service.logger.info(
                            "event=hotkey_release_dispatched action=%s", action
                        )
                    return 0

                # Pressed
                service._press_count += 1
                service.logger.info(
                    "event=hotkey_pressed action=%s label=%s count=%s",
                    action,
                    label,
                    service._press_count,
                )
                print(f"[vaani] hotkey pressed: {label} ({action})", flush=True)
                if action == "cancel":
                    if service.on_cancel is not None:
                        service.on_cancel()
                        service.logger.info("event=hotkey_cancel_dispatched")
                elif action == "approve":
                    if service.on_approve is not None:
                        service.on_approve()
                        service.logger.info("event=hotkey_approve_dispatched")
                else:
                    service._held_action = action
                    service.on_trigger(action)
                    service.logger.info(
                        "event=hotkey_trigger_dispatched action=%s", action
                    )
            except Exception as exc:
                service.logger.exception(
                    "event=hotkey_handler_error detail=%s", type(exc).__name__
                )
            return 0

        self._handler_proc = _handler
        # Register + install on the application target; dispatch via dispatcher.
        app_target = carbon.GetApplicationEventTarget()
        dispatcher = carbon.GetEventDispatcherTarget()
        self._app_target = int(app_target) if app_target else None
        self._dispatcher_target = int(dispatcher) if dispatcher else None
        self.logger.info(
            "event=hotkey_targets app=%s dispatcher=%s pressed_kind=%s released_kind=%s param=%s",
            self._app_target,
            self._dispatcher_target,
            _K_EVENT_HOT_KEY_PRESSED,
            _K_EVENT_HOT_KEY_RELEASED,
            _K_EVENT_PARAM_DIRECT_OBJECT,
        )
        if not app_target or not dispatcher:
            raise RuntimeError("GetApplicationEventTarget/GetEventDispatcherTarget failed")

        specs = (EventTypeSpec * 2)(
            EventTypeSpec(_K_EVENT_CLASS_KEYBOARD, _K_EVENT_HOT_KEY_PRESSED),
            EventTypeSpec(_K_EVENT_CLASS_KEYBOARD, _K_EVENT_HOT_KEY_RELEASED),
        )
        err = carbon.InstallEventHandler(
            app_target,
            self._handler_proc,
            2,
            specs,
            None,
            ctypes.byref(self._handler_ref),
        )
        self.logger.info("event=hotkey_install_handler status=%s ref=%s", err, self._handler_ref)
        if err != 0:
            raise RuntimeError(f"InstallEventHandler failed ({err})")

        signature = _fourcc("vani")
        for index, (key_code, modifiers, action, label) in enumerate(_BINDINGS):
            hotkey_id = EventHotKeyID(signature=signature, id=index)
            ref = ctypes.c_void_p()
            status = carbon.RegisterEventHotKey(
                key_code,
                modifiers,
                hotkey_id,
                app_target,
                0,
                ctypes.byref(ref),
            )
            if status != 0:
                self.logger.error(
                    "event=hotkey_register_failed action=%s label=%s key=%s mods=%s status=%s",
                    action,
                    label,
                    key_code,
                    modifiers,
                    status,
                )
                print(f"[vaani] FAILED to register {label} (status={status})", file=sys.stderr, flush=True)
                continue
            self._hotkey_refs.append(ref)
            self.logger.info(
                "event=hotkey_register_ok action=%s label=%s key=%s mods=%s",
                action,
                label,
                key_code,
                modifiers,
            )
            print(f"[vaani] registered {label}", flush=True)

        if not self._hotkey_refs:
            raise RuntimeError(
                "No macOS hotkeys registered — another app may own "
                "Option+Space / Option+Shift+Space / Control+Option+Space."
            )

        self.logger.info(
            "event=hotkey_armed count=%s pump=main_thread",
            len(self._hotkey_refs),
        )
        print(
            "Vaani hotkeys ready (hold-to-talk):\n"
            "  Hold Option+Space           → smart dictation\n"
            "  Hold Option+Shift+Space     → literal\n"
            "  Hold Control+Option+Space   → assistant\n"
            "  Esc                         → cancel\n"
            "Release the chord to stop — the pill vanishes on release.",
            flush=True,
        )

    def pump(self, timeout: float = 0.25) -> None:
        """Process pending Carbon events. Must run on the main thread."""
        if self._listener is not None or self._carbon is None or self._dispatcher_target is None:
            return
        carbon = self._carbon
        event = ctypes.c_void_p()
        self._pump_count += 1
        err = carbon.ReceiveNextEvent(0, None, float(timeout), True, ctypes.byref(event))
        if err == 0 and event:
            self.logger.debug("event=hotkey_loop_event pump=%s", self._pump_count)
            send_err = carbon.SendEventToEventTarget(event, self._dispatcher_target)
            carbon.ReleaseEvent(event)
            # eventNotHandledErr (-9874) is normal for unrelated events.
            if send_err not in (0, _EVENT_NOT_HANDLED):
                self.logger.warning("event=hotkey_send_failed status=%s", send_err)
            elif send_err == _EVENT_NOT_HANDLED:
                self.logger.debug("event=hotkey_send_unhandled pump=%s", self._pump_count)
        elif err == _EVENT_LOOP_TIMED_OUT:
            if self._pump_count in (1, 20, 100) or self._pump_count % 240 == 0:
                self.logger.debug(
                    "event=hotkey_loop_idle pump=%s presses=%s",
                    self._pump_count,
                    self._press_count,
                )
        else:
            if err != self._last_loop_error:
                self.logger.warning("event=hotkey_loop_error status=%s pump=%s", err, self._pump_count)
                self._last_loop_error = err

    def unregister(self) -> None:
        with self._lock:
            listener = self._listener
            self._listener = None
            refs = list(self._hotkey_refs)
            self._hotkey_refs.clear()
            handler_ref = self._handler_ref
            self._handler_ref = ctypes.c_void_p()
            carbon = self._carbon
            self._registered = False
            self._app_target = None
            self._dispatcher_target = None
            self._held_action = None
        self.logger.info(
            "event=hotkey_unregister presses=%s releases=%s pumps=%s",
            self._press_count,
            self._release_count,
            self._pump_count,
        )
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

    def _make_approve(self) -> Callable[[], None]:
        def _cb() -> None:
            if self.on_approve is None:
                return
            try:
                self.on_approve()
            except Exception:
                pass

        return _cb


HotkeyManager = HotkeyService
