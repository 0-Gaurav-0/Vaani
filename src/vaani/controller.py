"""Cancellable dictation lifecycle orchestration.

The controller deliberately depends on small adapter protocols, making the
hotkey path usable with real or test implementations.
"""
from __future__ import annotations

import logging
import threading
import time
import struct, math, os
from dataclasses import dataclass
from typing import Any, Callable

from .types import AppState, DictationMode
from .observability import exception_category, sanitize
from .groq import GroqError
from .apps import launch_app, resolve_app
from .sites import resolve_site

def normalize_answer_prefix(text: str) -> tuple[str | None, str]:
    import re
    m = re.match(r"^\s*(Answer this|Only answer|Question)\b\s*[:,\-]?\s*(.*)$", text, re.I | re.S)
    return (m.group(1), m.group(2).strip()) if m else (None, text.strip())


@dataclass(frozen=True)
class ControllerEvent:
    name: str
    state: AppState
    category: str | None = None


class Controller:
    def __init__(self, *, recorder: Any, groq: Any, delivery: Any,
                 history: Any, feedback: Any | None = None,
                 key_provider: Callable[[], str | None] | None = None,
                 hotkeys: Any | None = None, logger: logging.Logger | None = None,
                 max_duration: float = 300.0, join_timeout: float = 5.0,
                 codex: Any | None = None, result_window: Any | None = None,
                 amplitude_path: str | os.PathLike[str] | None = None,
                 browser_launcher: Any | None = None,
                 app_launcher: Any | None = None):
        self.recorder, self.groq, self.delivery, self.history = recorder, groq, delivery, history
        self.feedback, self.key_provider, self.hotkeys = feedback, key_provider or (lambda: None), hotkeys
        self.codex, self.result_window = codex, result_window
        self.browser_launcher = browser_launcher
        self.app_launcher = app_launcher
        self.amplitude_path = str(
            amplitude_path
            or os.environ.get("VAANI_AMPLITUDE_PATH")
            or "/tmp/vaani-amplitude"
        )
        self.logger = logger or logging.getLogger("vaani")
        self.max_duration, self.join_timeout = max_duration, join_timeout
        self.state = AppState.IDLE; self.mode: str | None = None
        self.events: list[ControllerEvent] = []; self._lock = threading.RLock()
        self._cancel = threading.Event(); self._token = 0; self._worker: threading.Thread | None = None
        self._record_started = 0.0; self._audio = None; self._shutdown = False
        self._amplitude_stop = threading.Event(); self._amplitude_thread = None

    def _emit(self, name: str, category: str | None = None) -> None:
        with self._lock: self.events.append(ControllerEvent(name, self.state, category))
        self.logger.info("event=%s state=%s category=%s", name, self.state.value, category or "")

    def trigger(self, mode: str = DictationMode.SMART.value) -> bool:
        with self._lock:
            if self._shutdown or self.state is not AppState.IDLE:
                self._emit("busy"); self._feedback("busy"); return False
            self.mode = mode.value if isinstance(mode, DictationMode) else str(mode)
            self._cancel.clear(); self._token += 1; token = self._token
            try: self._audio = self.recorder.start()
            except Exception as exc: self._fail(exc, "mic"); return False
            self.state = AppState.RECORDING; self._record_started = time.monotonic(); self._emit("recording")
            self._start_amplitude_monitor(self._audio.path)
            self._feedback("start")
            threading.Thread(target=self._duration_guard, args=(token,), daemon=True).start()
            return True

    start = trigger

    def trigger_assistant(self) -> bool:
        """Start an assistant request using the same recorder lifecycle."""
        return self.trigger("assistant")

    start_assistant = trigger_assistant

    def handle_hotkey(self, mode: str) -> bool:
        """Callback seam for :class:`HotkeyManager`. A second press stops capture."""
        with self._lock:
            recording = self.state is AppState.RECORDING
        return self.stop() if recording else self.trigger(mode)

    on_trigger = handle_hotkey

    def register_hotkeys(self) -> None:
        if self.hotkeys is None: return
        if hasattr(self.hotkeys, "on_trigger"): self.hotkeys.on_trigger = self.handle_hotkey
        if hasattr(self.hotkeys, "register"): self.hotkeys.register()

    def stop(self) -> bool:
        with self._lock:
            if self.state is not AppState.RECORDING: return False
            token = self._token; self.state = AppState.PROCESSING; self._emit("processing"); self._cancel.clear()
        self._feedback("processing")
        self._amplitude_stop.set()
        try: audio = self.recorder.stop()
        except Exception as exc: self._fail(exc, "mic"); return False
        worker = threading.Thread(target=self._process, args=(token, audio), daemon=True)
        self._worker = worker; worker.start(); return True

    def cancel(self) -> bool:
        self._cancel.set()
        self._amplitude_stop.set()
        with self._lock:
            if self.state is AppState.RECORDING:
                try: self.recorder.cleanup()
                except Exception: pass
                self.state = AppState.IDLE; self._emit("cancelled"); self._feedback("processing"); return True
            if self.state is AppState.PROCESSING:
                self._token += 1
                try:
                    if self.mode == "assistant" and self.codex is not None:
                        self.codex.cancel()
                except Exception:
                    pass
                self.state = AppState.IDLE
                self._feedback("processing"); self._emit("cancelled"); return True
        return False

    def _start_amplitude_monitor(self, path: Any) -> None:
        self._amplitude_stop.clear()
        out = self.amplitude_path
        def monitor():
            while not self._amplitude_stop.wait(0.08):
                try:
                    with open(path, "rb") as fh:
                        fh.seek(44); data = fh.read()[-2048:]
                    if len(data) >= 2:
                        vals = struct.unpack("<%dh" % (len(data)//2), data[:len(data)//2*2])
                        level = min(1.0, math.sqrt(sum(v*v for v in vals)/len(vals))/32768.0)
                        parent = os.path.dirname(out)
                        if parent:
                            os.makedirs(parent, mode=0o700, exist_ok=True)
                        with open(out, "w") as dst: dst.write(f"{level:.4f}")
                except Exception: pass
        self._amplitude_thread = threading.Thread(target=monitor, daemon=True); self._amplitude_thread.start()

    def _duration_guard(self, token: int) -> None:
        time.sleep(max(0.0, self.max_duration))
        with self._lock:
            if token != self._token or self.state is not AppState.RECORDING: return
        self.stop()

    def _process(self, token: int, audio: Any) -> None:
        try:
            key = self.key_provider()
            if not key: raise RuntimeError("API key required")
            result = self.groq.transcribe(audio.path, key, cancel=self._cancel, delete_audio=True,
                                          language="en" if self.mode == "assistant" else None)
            if self._cancel.is_set() or token != self._token: return
            raw = result.text; final = raw; cleanup_status = "skipped"; history_mode = self.mode or "literal"
            prefix, question = normalize_answer_prefix(raw)
            if prefix:
                answer = getattr(self.groq, "answer", None)
                if answer is None: raise RuntimeError("answer mode unavailable")
                final = answer(question, key, cancel=self._cancel).text
                history_mode = "answer"
            if self.mode == "assistant":
                app = (
                    self.app_launcher.resolve(raw)
                    if self.app_launcher is not None
                    else resolve_app(raw)
                )
                if app:
                    answer = (
                        self.app_launcher.launch(app)
                        if self.app_launcher is not None
                        else launch_app(app)
                    )
                    if self.result_window is not None and hasattr(self.result_window, "show_text"):
                        self.result_window.show_text(answer)
                    with self._lock:
                        if self._cancel.is_set() or token != self._token: return
                        self.history.insert(raw_text=raw, final_text=answer, mode="assistant",
                                            delivery_status="displayed", cleanup_status="app_action",
                                            duration_ms=int(getattr(audio, "duration_seconds", 0) * 1000))
                        self.state = AppState.IDLE; self._emit("assistant_complete"); self._feedback("success")
                    return
                site = resolve_site(raw)
                browser = self._browser_intent(raw)
                if site or browser:
                    url = site.url if site else "about:blank"
                    requested = site.browser if site else ("chrome" if "chrome" in raw.casefold() else "brave")
                    prefer = "chrome" if requested == "chrome" else "brave"
                    if self.browser_launcher is not None:
                        answer = self.browser_launcher.open(url, prefer=prefer)
                    else:
                        answer = self._open_browser(prefer_brave=prefer != "chrome", url=url)
                    if site and answer.startswith("Opened"):
                        answer = f"Opened {site.name}."
                    if self.result_window is not None and hasattr(self.result_window, "show_text"):
                        self.result_window.show_text(answer)
                    with self._lock:
                        if self._cancel.is_set() or token != self._token: return
                        self.history.insert(raw_text=raw, final_text=answer, mode="assistant",
                                            delivery_status="displayed", cleanup_status="browser_action",
                                            duration_ms=int(getattr(audio, "duration_seconds", 0) * 1000))
                        self.state = AppState.IDLE; self._emit("assistant_complete"); self._feedback("success")
                    return
                if self.codex is None:
                    raise RuntimeError("assistant runner unavailable")
                answer = self.codex.run(raw)
                if self.result_window is not None and hasattr(self.result_window, "show"):
                    self.result_window.show(answer)
                if getattr(answer, "cancelled", False) or getattr(answer, "timed_out", False):
                    raise RuntimeError("assistant request cancelled or timed out")
                final = answer.stdout
                with self._lock:
                    if self._cancel.is_set() or token != self._token: return
                    self.history.insert(raw_text=raw, final_text=final, mode="assistant",
                                        delivery_status="displayed", cleanup_status="skipped",
                                        duration_ms=int(getattr(audio, "duration_seconds", 0) * 1000))
                    self.state = AppState.IDLE; self._emit("assistant_complete"); self._feedback("success")
                return
            if self.mode != DictationMode.LITERAL.value:
                cleaned = self.groq.cleanup(raw, key, cancel=self._cancel); final = cleaned.text
                cleanup_status = "fallback" if cleaned.used_fallback else "cleaned"
            if self._cancel.is_set() or token != self._token: return
            snapshot = None
            target = getattr(self.delivery, "target", None)
            capture = getattr(target, "snapshot", None)
            if capture is not None:
                try:
                    snapshot = capture()
                except Exception:
                    snapshot = None
            try:
                status = self.delivery.deliver(final, snapshot=snapshot)
            except TypeError as exc:
                # Preserve compatibility with simple delivery test adapters.
                if "snapshot" not in str(exc):
                    raise
                status = self.delivery.deliver(final)
            # Delivery may block or cancellation may arrive concurrently; never
            # record/announce a result from an invalidated operation.
            with self._lock:
                if self._cancel.is_set() or token != self._token: return
                self.history.insert(raw_text=raw, final_text=final, mode=history_mode,
                                    detected_language=getattr(result, "language", None),
                                    delivery_status=getattr(status, "value", str(status)), cleanup_status=cleanup_status,
                                    duration_ms=int(getattr(audio, "duration_seconds", 0) * 1000))
                self.state = AppState.IDLE; self._emit("delivered")
                self._feedback("success")
        except Exception as exc:
            if self._cancel.is_set(): return
            try: self.recorder.cleanup()
            except Exception: pass
            self._fail(exc, getattr(exc, "category", None))

    @staticmethod
    def _browser_intent(text: str) -> bool:
        normalized = " ".join(text.casefold().strip().split())
        direct = {"open chrome", "open google chrome", "open browser", "launch chrome", "launch browser",
                  "open brave", "launch brave", "open brave browser", "launch brave browser"}
        return normalized in direct

    @staticmethod
    def _open_browser(*, prefer_brave: bool = True, url: str = "about:blank") -> str:
        """Backward-compatible Linux browser open used by unit tests and fallbacks."""
        from .platform.linux.browser import open_browser

        return open_browser(prefer_brave=prefer_brave, url=url)

    def _fail(self, exc: BaseException, category: str | None = None) -> None:
        category = category or exception_category(exc)
        with self._lock: self.state = AppState.IDLE; self._emit("failure", category)
        self.logger.error("controller failure category=%s detail=%s", category, sanitize(exc))
        self._feedback(category or "failure")

    def _feedback(self, cue: str) -> None:
        try:
            if self.feedback and hasattr(self.feedback, "play"): self.feedback.play(cue)
            elif self.feedback and hasattr(self.feedback, "notify"): self.feedback.notify(cue, "")
        except Exception: pass

    def shutdown(self) -> None:
        with self._lock:
            if self._shutdown: return
            self._shutdown = True; self._token += 1; self._cancel.set()
        for obj, method in ((self.hotkeys, "unregister"), (self.recorder, "cleanup"), (self.delivery, "cancel")):
            try:
                if obj and hasattr(obj, method): getattr(obj, method)()
            except Exception: pass
        worker = self._worker
        if worker and worker.is_alive(): worker.join(timeout=self.join_timeout)
        blocked = bool(worker and worker.is_alive())
        if blocked: self.logger.error("forced shutdown: blocked daemon worker")
        # Closing transports while their worker is blocked can trigger late
        # callbacks; leave them owned by the daemon and let process exit reap.
        for obj in (() if blocked else (self.groq, self.delivery, self.history)):
            try:
                if hasattr(obj, "close"): obj.close()
                elif hasattr(obj, "shutdown"): obj.shutdown()
            except Exception: pass
        with self._lock: self.state = AppState.IDLE; self._emit("shutdown")

    close = shutdown


# Compatibility names used by integrations and older launchers.
ControllerStateMachine = Controller
VaaniController = Controller
