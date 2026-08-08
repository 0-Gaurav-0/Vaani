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

from pathlib import Path

from .types import AppState, DictationMode
from .observability import exception_category, sanitize
from .groq import GroqError, guard_transcription, light_local_cleanup
from .audio import is_silent_wav
from .apps import launch_app, resolve_app, resolve_app_name
from .folders import launch_folder, resolve_folder, resolve_folder_name
from .volume import resolve_volume_action, run_volume_action
from .media import resolve_media_action, run_media_action
from .sites import resolve_site, resolve_youtube, resolve_play_target, resolve_browse_query
from .skills import load_skill_index, match_skill
from .codex import build_skill_prompt
from .assistant_intent import (
    AGENT_MUTATION_REFUSE,
    classify_assistant_intent,
    extract_agent_handoff,
    extract_session_continue,
    looks_like_action,
    looks_like_agent_mutation,
    clean_play_query,
    is_weak_play_query,
    with_vaani_source_tag,
    with_work_context_hint,
)
from .handoff_memory import list_recent_handoffs, match_handoff, remember_handoff
from .assistant_route import (
    RouteDecision,
    RouteOption,
    default_media_options,
    default_open_options,
    format_clarify_body,
    match_clarify_choice,
)
from .indicator_protocol import clear_command, read_command
from .delivery import DeliveryStatus
from .memory import append_turn, load_context

# Temporary diagnostic: set VAANI_RAW_STT=1 to paste Whisper output with zero
# transcript post-processing (no guard, romanize/pick, cleanup, or answer-prefix).
RAW_STT_NO_POSTPROCESS = os.environ.get("VAANI_RAW_STT", "").strip().lower() in {
    "1",
    "true",
    "yes",
}


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
                 max_duration: float = 600.0, join_timeout: float = 5.0,
                 codex: Any | None = None, result_window: Any | None = None,
                 amplitude_path: str | os.PathLike[str] | None = None,
                 indicator_control_path: str | os.PathLike[str] | None = None,
                 browser_launcher: Any | None = None,
                 app_launcher: Any | None = None,
                 media_keys: Any | None = None):
        self.recorder, self.groq, self.delivery, self.history = recorder, groq, delivery, history
        self.feedback, self.key_provider, self.hotkeys = feedback, key_provider or (lambda: None), hotkeys
        self.codex, self.result_window = codex, result_window
        self.browser_launcher = browser_launcher
        self.app_launcher = app_launcher
        self.media_keys = media_keys
        self.amplitude_path = str(
            amplitude_path
            or os.environ.get("VAANI_AMPLITUDE_PATH")
            or "/tmp/vaani-amplitude"
        )
        if indicator_control_path is not None:
            self.indicator_control_path = str(indicator_control_path)
        else:
            env_control = os.environ.get("VAANI_INDICATOR_CONTROL")
            if env_control:
                self.indicator_control_path = env_control
            else:
                self.indicator_control_path = str(
                    Path(self.amplitude_path).parent / "indicator_control.json"
                )
        self.logger = logger or logging.getLogger("vaani")
        self.max_duration, self.join_timeout = max_duration, join_timeout
        self.state = AppState.IDLE; self.mode: str | None = None
        self.events: list[ControllerEvent] = []; self._lock = threading.RLock()
        self._cancel = threading.Event(); self._token = 0; self._worker: threading.Thread | None = None
        self._record_started = 0.0; self._audio = None; self._shutdown = False
        self._amplitude_stop = threading.Event(); self._amplitude_thread = None
        self._pending_clarify: tuple[RouteOption, ...] | None = None
        self._clarify_raw = ""
        self._clarify_stop = threading.Event()
        self._clarify_thread: threading.Thread | None = None
        self._handoff_job: Any | None = None
        # True when the current RECORDING session was opened by a finger-snap
        # (so TrackPoint press can complete it — gesture manager has no session).
        self._snap_armed_session = False
        self._snap_watcher: Any = None

    def _emit(self, name: str, category: str | None = None) -> None:
        with self._lock: self.events.append(ControllerEvent(name, self.state, category))
        self.logger.info("event=%s state=%s category=%s", name, self.state.value, category or "")

    def trigger(self, mode: str = DictationMode.SMART.value) -> bool:
        with self._lock:
            if self._shutdown:
                return False
            # While Groq is working, ignore new dictation — keep the processing pill.
            if self.state is AppState.PROCESSING:
                self._emit("busy")
                # Avoid flooding the log when the middle button is held through processing.
                if not getattr(self, "_logged_processing_block", False):
                    self.logger.info("event=input_blocked reason=processing")
                    self._logged_processing_block = True
                return False
            self._logged_processing_block = False
            if self.state is not AppState.IDLE:
                self._emit("busy")
                self._feedback("busy")
                return False
            self.mode = mode.value if isinstance(mode, DictationMode) else str(mode)
            self._cancel.clear(); self._token += 1; token = self._token
            arm = getattr(self.delivery, "arm", None)
            if callable(arm):
                try:
                    arm()
                except Exception:
                    pass
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

    def _stop_snap_watcher(self) -> None:
        watcher = self._snap_watcher
        self._snap_watcher = None
        if watcher is not None:
            try:
                watcher.stop()
            except Exception:
                pass

    def _arm_snap_session_watcher(self) -> None:
        """Detect stop-snap / trailing silence from the recording WAV itself."""
        self._stop_snap_watcher()
        path = getattr(getattr(self, "_audio", None), "path", None)
        if path is None:
            return
        from .snap_endpoint import SnapSessionWatcher

        def _stop_from_endpoint() -> None:
            with self._lock:
                if self.state is not AppState.RECORDING:
                    return
                if not self._snap_armed_session:
                    return
            self.logger.info("event=snap_stop source=endpoint")
            self.stop()

        def _cancel_from_endpoint() -> None:
            with self._lock:
                if self.state is not AppState.RECORDING:
                    return
                if not self._snap_armed_session:
                    return
            self.logger.info("event=snap_cancel source=endpoint")
            self.cancel()

        watcher = SnapSessionWatcher(
            path,
            on_stop=_stop_from_endpoint,
            on_cancel=_cancel_from_endpoint,
            should_run=lambda: self._snap_armed_session
            and self.state is AppState.RECORDING,
        )
        self._snap_watcher = watcher
        watcher.start()

    def snap_assistant_toggle(self) -> bool:
        """Finger-snap: start assistant capture, or stop if already recording."""
        with self._lock:
            if self.state is AppState.PROCESSING:
                self.logger.info("event=snap_ignored reason=processing")
                return False
            recording = self.state is AppState.RECORDING
        if recording:
            self.logger.info("event=snap_stop source=toggle")
            ok = self.stop()
            self._snap_armed_session = False
            return ok
        ok = self.trigger_assistant()
        if ok:
            self._snap_armed_session = True
            self.logger.info("event=snap_start mode=assistant")
            self._arm_snap_session_watcher()
        return ok

    def handle_wake_phrase(self, payload: str) -> bool:
        """Hey Vaani: bare wake starts capture; wake+command runs assistant now."""
        with self._lock:
            if self._shutdown or self.state is not AppState.IDLE:
                self.logger.info(
                    "event=wake_ignored state=%s",
                    getattr(self.state, "name", self.state),
                )
                return False
        text = " ".join((payload or "").split()).strip()
        if not text:
            ok = self.trigger_assistant()
            if ok:
                self._snap_armed_session = True
                self.logger.info("event=wake_start mode=assistant")
                self._arm_snap_session_watcher()
            return ok
        return self._start_assistant_text(text)

    def _start_assistant_text(self, text: str) -> bool:
        """Run the assistant pipeline on an already-transcribed wake command."""
        with self._lock:
            if self.state is not AppState.IDLE:
                return False
            self.mode = "assistant"
            self._cancel.clear()
            self._token += 1
            token = self._token
            self.state = AppState.PROCESSING
            self._emit("processing")
        self._feedback("processing")
        self.logger.info(
            "event=wake_command chars=%s preview=%r",
            len(text),
            (text[:80] + "…") if len(text) > 80 else text,
        )
        key = self.key_provider() or ""
        audio = type("Audio", (), {"duration_seconds": 0.0, "path": Path("/dev/null")})()
        result = type("Result", (), {"text": text, "language": "wake"})()

        def worker() -> None:
            try:
                self._process_assistant(token, audio, key, text, result)
            except Exception as exc:
                if not self._cancel.is_set():
                    self._fail(exc, getattr(exc, "category", None))
            finally:
                with self._lock:
                    if token == self._token and self.state is AppState.PROCESSING:
                        self.state = AppState.IDLE
                        self._emit("assistant_complete")

        threading.Thread(target=worker, name="vaani-wake-cmd", daemon=True).start()
        return True

    def handle_hotkey(self, mode: str) -> bool:
        """Callback seam for :class:`HotkeyManager`. A second press stops capture."""
        with self._lock:
            if self.state is AppState.PROCESSING:
                self._emit("busy")
                self.logger.info("event=input_blocked reason=processing")
                return False
            recording = self.state is AppState.RECORDING
        if recording:
            self._snap_armed_session = False
            return self.stop()
        return self.trigger(mode)

    on_trigger = handle_hotkey

    def register_hotkeys(self) -> None:
        if self.hotkeys is None: return
        if hasattr(self.hotkeys, "on_trigger"): self.hotkeys.on_trigger = self.handle_hotkey
        if hasattr(self.hotkeys, "register"): self.hotkeys.register()

    def stop(self) -> bool:
        with self._lock:
            if self.state is not AppState.RECORDING: return False
            token = self._token; self.state = AppState.PROCESSING; self._emit("processing"); self._cancel.clear()
            self._snap_armed_session = False
        self._stop_snap_watcher()
        # Keep the session monitor alive during PROCESSING so the pill can
        # cancel, and so we can show a processing phase. Mic is released below.
        self._feedback("processing")
        try: audio = self.recorder.stop()
        except Exception as exc:
            self._amplitude_stop.set()
            # Accidental click / bounce: treat too-short clips as a quiet cancel.
            detail = str(exc).lower()
            if "duration out of range" in detail or "invalid audio" in detail:
                try: self.recorder.cleanup()
                except Exception: pass
                with self._lock:
                    self.state = AppState.IDLE
                    self._snap_armed_session = False
                self._emit("cancelled")
                self._feedback("busy")
                self.logger.info("event=recording_ignored reason=too_short")
                return False
            self._fail(exc, "mic")
            return False
        worker = threading.Thread(target=self._process, args=(token, audio), daemon=True)
        self._worker = worker; worker.start(); return True

    def cancel(self) -> bool:
        self.logger.info(
            "event=cancel_begin state=%s snap=%s",
            getattr(self.state, "name", self.state),
            self._snap_armed_session,
        )
        self._cancel.set()
        self._amplitude_stop.set()
        self._clear_clarify()
        self._snap_armed_session = False
        self._stop_snap_watcher()
        try:
            if self.delivery is not None and hasattr(self.delivery, "cancel"):
                self.delivery.cancel()
        except Exception:
            pass
        worker = None
        handled = False
        with self._lock:
            if self.state is AppState.RECORDING:
                try: self.recorder.cleanup()
                except Exception: pass
                self.state = AppState.IDLE; self._emit("cancelled"); self._feedback("busy"); return True
            if self.state is AppState.PROCESSING:
                self._token += 1
                job = self._handoff_job
                self._handoff_job = None
                try:
                    if job is not None and hasattr(job, "cancel"):
                        job.cancel()
                except Exception:
                    pass
                try:
                    if self.mode == "assistant" and self.codex is not None:
                        self.codex.cancel()
                except Exception:
                    pass
                try:
                    if self.groq is not None and hasattr(self.groq, "reset"):
                        self.groq.reset()
                except Exception:
                    pass
                self.state = AppState.IDLE
                self._feedback("busy"); self._emit("cancelled")
                worker = self._worker
                handled = True
            else:
                self.logger.info("event=cancel_ignored state=%s", getattr(self.state, "name", self.state))
        if handled and worker and worker.is_alive():
            worker.join(timeout=min(1.0, self.join_timeout))
        return handled

    def _clear_clarify(self) -> None:
        self._clarify_stop.set()
        with self._lock:
            self._pending_clarify = None
            self._clarify_raw = ""

    def _poll_indicator_control(self) -> None:
        """Honor stop/cancel/option requests from the floating recording pill."""
        try:
            command = read_command(self.indicator_control_path)
        except Exception:
            return
        if command is None:
            return
        try:
            clear_command(self.indicator_control_path)
        except Exception:
            pass
        if command == "stop":
            self.stop()
        elif command == "cancel":
            self.cancel()
        elif isinstance(command, str) and command.startswith("option_"):
            try:
                idx = int(command.split("_", 1)[1])
            except (IndexError, ValueError):
                return
            self._apply_clarify_index(idx)

    def _start_amplitude_monitor(self, path: Any) -> None:
        self._amplitude_stop.clear()
        try:
            clear_command(self.indicator_control_path)
        except Exception:
            pass
        out = self.amplitude_path
        def monitor():
            while not self._amplitude_stop.wait(0.05):
                self._poll_indicator_control()
                with self._lock:
                    recording = self.state is AppState.RECORDING
                if not recording:
                    continue
                try:
                    level = None
                    # Prefer live PortAudio level (macOS/Windows buffer-in-memory).
                    live = getattr(self.recorder, "level", None)
                    if isinstance(live, (int, float)):
                        level = float(live)
                    if level is None:
                        with open(path, "rb") as fh:
                            fh.seek(44)
                            data = fh.read()[-2048:]
                        if len(data) >= 2:
                            vals = struct.unpack(
                                "<%dh" % (len(data) // 2), data[: len(data) // 2 * 2]
                            )
                            level = min(
                                1.0,
                                math.sqrt(sum(v * v for v in vals) / len(vals)) / 32768.0,
                            )
                    if level is not None:
                        parent = os.path.dirname(out)
                        if parent:
                            os.makedirs(parent, mode=0o700, exist_ok=True)
                        with open(out, "w") as dst:
                            dst.write(f"{level:.4f}")
                except Exception:
                    pass
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
            try:
                size = Path(audio.path).stat().st_size
            except OSError:
                size = -1
            self.logger.info(
                "event=process_start mode=%s duration=%.2fs bytes=%s",
                self.mode,
                float(getattr(audio, "duration_seconds", 0) or 0),
                size,
            )
            if is_silent_wav(Path(audio.path)):
                self.logger.info("event=transcription_rejected reason=silence")
                try:
                    Path(audio.path).unlink(missing_ok=True)
                except OSError:
                    pass
                with self._lock:
                    if self._cancel.is_set() or token != self._token:
                        return
                    self.state = AppState.IDLE
                    self._emit("cancelled")
                self._feedback("busy")
                return
            if RAW_STT_NO_POSTPROCESS:
                # One Whisper call only — paste exactly what the model returned.
                result = self.groq.transcribe(
                    audio.path,
                    key,
                    cancel=self._cancel,
                    delete_audio=True,
                    language=None,
                    prompt=None,
                )
                if self._cancel.is_set() or token != self._token:
                    return
                raw = (result.text or "").strip()
                self.logger.info(
                    "event=raw_stt_no_postprocess chars=%s preview=%r",
                    len(raw),
                    (raw[:120] + "…") if len(raw) > 120 else raw,
                )
                if not raw:
                    with self._lock:
                        if self._cancel.is_set() or token != self._token:
                            return
                        self.state = AppState.IDLE
                        self._emit("cancelled")
                    self._feedback("busy")
                    return
                if self.mode == "assistant":
                    self._process_assistant(token, audio, key, raw, result)
                    return
                self._deliver_text(
                    token,
                    audio,
                    raw=raw,
                    final=raw,
                    history_mode=self.mode or "literal",
                    cleanup_status="raw_no_postprocess",
                    language=getattr(result, "language", None),
                )
                return
            # Gemini (or Groq hinglish) transcription.
            transcribe_h = getattr(self.groq, "transcribe_hinglish", None)
            if callable(transcribe_h):
                result = transcribe_h(
                    audio.path, key, cancel=self._cancel, delete_audio=True
                )
            else:
                result = self.groq.transcribe(
                    audio.path, key, cancel=self._cancel, delete_audio=True, language="en"
                )
            if self._cancel.is_set() or token != self._token: return
            raw = (result.text or "").strip()
            gemini_raw = getattr(result, "language", None) == "gemini"
            if gemini_raw:
                # Iteration: API text only — no guard / cleanup / answer-prefix.
                self.logger.info(
                    "event=gemini_raw_deliver chars=%s preview=%r",
                    len(raw),
                    (raw[:120] + "…") if len(raw) > 120 else raw,
                )
                if not raw:
                    with self._lock:
                        if self._cancel.is_set() or token != self._token:
                            return
                        self.state = AppState.IDLE
                        self._emit("cancelled")
                    self._feedback("busy")
                    return
                if self.mode == "assistant":
                    self._process_assistant(token, audio, key, raw, result)
                    return
                self._deliver_text(
                    token,
                    audio,
                    raw=raw,
                    final=raw,
                    history_mode=self.mode or "literal",
                    cleanup_status="gemini_raw",
                    language="gemini",
                )
                return
            guarded = guard_transcription(raw) if raw else None
            if guarded is None:
                self.logger.info("event=transcription_rejected reason=prompt_bleed")
                with self._lock:
                    if self._cancel.is_set() or token != self._token: return
                    self.state = AppState.IDLE
                    self._emit("cancelled")
                self._feedback("busy")
                return
            raw = guarded
            final = raw
            cleanup_status = "skipped"
            history_mode = self.mode or "literal"
            answered_via_prefix = False
            prefix, question = normalize_answer_prefix(raw)
            if prefix:
                answer = getattr(self.groq, "answer", None)
                if answer is None: raise RuntimeError("answer mode unavailable")
                final = answer(question, key, cancel=self._cancel).text
                history_mode = "answer"
                answered_via_prefix = True
            if self.mode == "assistant":
                self._process_assistant(token, audio, key, raw, result)
                return
            # Exact-words dictation: never LLM-rewrite the transcript.
            # (Cleanup was translating / paraphrasing Hinglish mixes.)
            if not answered_via_prefix:
                local = light_local_cleanup(raw)
                if local:
                    final = local
                cleanup_status = "local_only"
            if self._cancel.is_set() or token != self._token: return
            self._deliver_text(
                token,
                audio,
                raw=raw,
                final=final,
                history_mode=history_mode,
                cleanup_status=cleanup_status,
                language=getattr(result, "language", None),
            )
        except Exception as exc:
            if self._cancel.is_set():
                return
            try:
                self.recorder.cleanup()
            except Exception:
                pass
            self._fail(exc, getattr(exc, "category", None))
        finally:
            # End session monitor after processing (pill goes away via feedback).
            self._amplitude_stop.set()

    def _process_assistant(self, token: int, audio: Any, key: str, raw: str, result: Any) -> None:
        # Resolve a pending clarify choice by speech before anything else.
        pending: tuple[RouteOption, ...] | None = None
        with self._lock:
            pending = self._pending_clarify
        if pending:
            picked = match_clarify_choice(raw, pending)
            # New action command while clarify is open → don't treat it as picking
            # a Q&A option (e.g. garbled "play …" matching label "Kya haal hai?").
            if (
                picked is not None
                and looks_like_action(raw)
                and picked.intent in {"qa", "paste", "clarify"}
            ):
                self.logger.info(
                    "event=assistant_clarify_abandoned reason=new_action label=%s",
                    picked.label,
                )
                self._clear_clarify()
                pending = None
                picked = None
            elif picked is not None:
                self._clear_clarify()
                self.logger.info(
                    "event=assistant_clarify_pick via=speech label=%s", picked.label
                )
                decision = RouteDecision(
                    intent=picked.intent,
                    query=picked.query,
                    target=picked.target,
                    confidence=1.0,
                )
                self._execute_route_decision(token, audio, key, raw, result, decision)
                return
            else:
                self._clear_clarify()
                self.logger.info("event=assistant_clarify_abandoned")
                pending = None

        # Wake-shaped STT ("Hey Vani, who is…") still includes the wake words
        # when the mic stays open after "hey Vaani". Strip them so a normal
        # question is QA locally — not a Hermes handoff.
        try:
            from .wake_phrase import extract_wake_assistant

            wake_payload = extract_wake_assistant(raw)
        except Exception:
            wake_payload = None
        if wake_payload is not None and wake_payload.strip():
            raw = wake_payload.strip()

        # Explicit agent handoff ("ask Vaani…", "say hi to the agent") — skip
        # open/play/clarify and forward straight to Hermes (no confirm gate).
        handoff = extract_agent_handoff(raw)
        if handoff is not None:
            # "ask Vaani continue …" / follow-up inside an explicit handoff.
            cont = extract_session_continue(handoff)
            if cont is not None:
                self._assistant_resume_or_clarify(
                    token, audio, raw, follow_up=cont or handoff
                )
                return
            self.logger.info(
                "event=assistant_agent_handoff chars=%s preview=%r",
                len(handoff),
                (handoff[:80] + "…") if len(handoff) > 80 else handoff,
            )
            self._assistant_codex(token, audio, handoff, confirmed=True)
            return

        # Same-session follow-up ("continue…", "update on that…") — resume or clarify.
        # Fast path: deterministic resolvers (no LLM).
        # Media before session-continue so "resume" / "continue playing"
        # hit playback, not Hermes resume.
        if self._assistant_try_volume(token, audio, raw):
            return
        if self._assistant_try_media(token, audio, raw):
            return

        cont = extract_session_continue(raw)
        if cont is not None:
            self._assistant_resume_or_clarify(
                token, audio, raw, follow_up=cont or raw
            )
            return

        if self._assistant_try_app(token, audio, raw):
            return
        if self._assistant_try_folder(token, audio, raw):
            return
        if self._assistant_try_browser(token, audio, raw):
            return
        if self._assistant_try_skill(token, audio, raw):
            return

        route_fn = getattr(self.groq, "route", None)
        decision: RouteDecision | None = None
        if callable(route_fn):
            try:
                decision = route_fn(raw, key, cancel=self._cancel)
            except Exception as exc:
                self.logger.warning(
                    "event=assistant_route_failed detail=%s", type(exc).__name__
                )
                decision = None

        if decision is None:
            # Fallback to legacy heuristic classification.
            intent = classify_assistant_intent(raw)
            self.logger.info("event=assistant_intent kind=%s chars=%s", intent, len(raw))
            if intent == "qa":
                self._assistant_qa(token, audio, key, raw, result)
                return
            if intent == "codex":
                # Heuristic "needs agent" → same async Hermes handoff as ask Vaani.
                self._assistant_codex(token, audio, raw, confirmed=True)
                return
            self._deliver_text(
                token,
                audio,
                raw=raw,
                final=raw,
                history_mode="assistant",
                cleanup_status="skipped",
                language=getattr(result, "language", None),
                emit_name="assistant_complete",
            )
            return

        self.logger.info(
            "event=assistant_intent kind=%s target=%s confidence=%.2f chars=%s",
            decision.intent,
            decision.target or "-",
            decision.confidence,
            len(raw),
        )
        if decision.should_clarify:
            options = decision.options
            if not options and decision.query:
                if decision.intent == "open" or decision.target in {"app", "site"}:
                    options = default_open_options(decision.query)
                else:
                    options = default_media_options(decision.query)
            if not options:
                # Ambiguous utterance: offer both open and media-ish choices.
                options = default_open_options(raw)[:2] + default_media_options(raw)[:2]
            self._begin_clarify(token, audio, raw, options)
            return

        self._execute_route_decision(token, audio, key, raw, result, decision)

    def _begin_clarify(
        self,
        token: int,
        audio: Any,
        raw: str,
        options: tuple[RouteOption, ...] | list[RouteOption],
    ) -> None:
        opts = tuple(options)[:5]
        self.logger.info("event=assistant_clarify options=%s", len(opts))
        with self._lock:
            self._pending_clarify = opts
            self._clarify_raw = raw
            self._clarify_stop.clear()
            if self._cancel.is_set() or token != self._token:
                return
            self.history.insert(
                raw_text=raw,
                final_text=format_clarify_body(opts),
                mode="assistant",
                delivery_status="displayed",
                cleanup_status="clarify",
                duration_ms=int(getattr(audio, "duration_seconds", 0) * 1000),
            )
            self.state = AppState.IDLE
            self._emit("assistant_complete")
        show = getattr(self.feedback, "show_clarify", None)
        if callable(show):
            show(raw, [o.label for o in opts])
        else:
            show_ans = getattr(self.feedback, "show_answer", None)
            if callable(show_ans):
                show_ans(raw, format_clarify_body(opts))
        self._start_clarify_poller()

    def _start_clarify_poller(self) -> None:
        self._clarify_stop.clear()

        def monitor() -> None:
            deadline = time.monotonic() + 45.0
            while not self._clarify_stop.wait(0.1):
                if time.monotonic() >= deadline:
                    self.logger.info("event=assistant_clarify_timeout")
                    self._clear_clarify()
                    return
                with self._lock:
                    if not self._pending_clarify:
                        return
                self._poll_indicator_control()

        self._clarify_thread = threading.Thread(target=monitor, daemon=True)
        self._clarify_thread.start()

    def _apply_clarify_index(self, idx: int) -> None:
        with self._lock:
            options = self._pending_clarify
            raw = self._clarify_raw
        if not options or idx < 0 or idx >= len(options):
            return
        picked = options[idx]
        self._clear_clarify()
        self.logger.info(
            "event=assistant_clarify_pick via=click label=%s", picked.label
        )
        key = self.key_provider() or ""
        decision = RouteDecision(
            intent=picked.intent,
            query=picked.query,
            target=picked.target,
            confidence=1.0,
        )
        audio = type("Audio", (), {"duration_seconds": 0.0, "path": Path("/dev/null")})()
        result = type("Result", (), {"language": None})()
        with self._lock:
            self._token += 1
            token = self._token
            self.state = AppState.PROCESSING
        try:
            self._execute_route_decision(token, audio, key, raw, result, decision)
        except Exception as exc:
            self._fail(exc, getattr(exc, "category", None))

    def _execute_route_decision(
        self,
        token: int,
        audio: Any,
        key: str,
        raw: str,
        result: Any,
        decision: RouteDecision,
    ) -> None:
        intent = decision.intent
        query = decision.query or raw
        if (decision.target or "").casefold() == "cancel":
            with self._lock:
                if self._cancel.is_set() or token != self._token:
                    return
                self.history.insert(
                    raw_text=raw,
                    final_text="Cancelled.",
                    mode="assistant",
                    delivery_status="displayed",
                    cleanup_status="confirm_cancel",
                    duration_ms=int(getattr(audio, "duration_seconds", 0) * 1000),
                )
                self.state = AppState.IDLE
                self._emit("assistant_complete")
            self._feedback("busy")
            return

        if intent == "play":
            # Local YouTube play is allowed. Only skip silence/lyric junk so we
            # don't spam tabs — do NOT require a perfect play verb (STT mangles
            # "play sanghu tere" → "plesa sanghoote de"). Hermes read-only is
            # enforced separately in _assistant_codex.
            play_query = clean_play_query(query, fallback=raw)
            if is_weak_play_query(play_query, raw=raw):
                self.logger.info(
                    "event=assistant_play_rejected reason=weak_query "
                    "chars=%s preview=%r",
                    len(raw),
                    (raw[:80] + "…") if len(raw) > 80 else raw,
                )
                with self._lock:
                    if self._cancel.is_set() or token != self._token:
                        return
                    self.history.insert(
                        raw_text=raw,
                        final_text="",
                        mode="assistant",
                        delivery_status="rejected",
                        cleanup_status="play_weak_query",
                        duration_ms=int(
                            getattr(audio, "duration_seconds", 0) * 1000
                        ),
                    )
                    self.state = AppState.IDLE
                    self._emit("cancelled")
                self._feedback("busy")
                return
            site = resolve_play_target(play_query, decision.target or "youtube")
            if site and self._assistant_open_site(token, audio, raw, site):
                return
            self._deliver_text(
                token, audio, raw=raw, final=raw, history_mode="assistant",
                cleanup_status="skipped", language=getattr(result, "language", None),
                emit_name="assistant_complete",
            )
            return

        if intent == "open":
            browser = None
            if "brave" in raw.casefold():
                browser = "brave"
            elif "chrome" in raw.casefold():
                browser = "chrome"
            want = (decision.target or "").casefold()
            # Prefer desktop app when requested or when the name matches an app.
            if want in {"", "app"}:
                app = resolve_app_name(query)
                if app is not None:
                    open_text = f"open {app.name}"
                    if self._assistant_try_app(token, audio, open_text):
                        return
                folder = resolve_folder_name(query)
                if folder is not None:
                    open_text = f"open {folder.name}"
                    if self._assistant_try_folder(token, audio, open_text):
                        return
            if want in {"", "site", "web", "browser"}:
                site = resolve_browse_query(query, browser=browser)
                if site and self._assistant_open_site(token, audio, raw, site):
                    return
            # Last resort: synthesize classic open phrases for resolvers.
            open_text = f"open {query}".strip()
            if self._assistant_try_app(token, audio, open_text):
                return
            if self._assistant_try_folder(token, audio, open_text):
                return
            if self._assistant_try_browser(token, audio, open_text):
                return
            self._deliver_text(
                token, audio, raw=raw, final=raw, history_mode="assistant",
                cleanup_status="skipped", language=getattr(result, "language", None),
                emit_name="assistant_complete",
            )
            return

        if intent == "qa":
            self._assistant_qa(token, audio, key, query or raw, result)
            return

        if intent == "codex":
            resume = None
            target = (decision.target or "").strip()
            if target.startswith("resume:"):
                resume = target.split(":", 1)[1].strip() or None
            # Router chose agent work → fire-and-forget Hermes (same as ask Vaani).
            self._assistant_codex(
                token,
                audio,
                query or raw,
                confirmed=True,
                resume_session=resume,
            )
            return

        if intent == "skill":
            if self._assistant_try_skill(token, audio, query or raw):
                return
            self._assistant_codex(token, audio, query or raw, confirmed=True)
            return

        # paste / unknown
        self._deliver_text(
            token,
            audio,
            raw=raw,
            final=query or raw,
            history_mode="assistant",
            cleanup_status="skipped",
            language=getattr(result, "language", None),
            emit_name="assistant_complete",
        )

    def _assistant_qa(
        self, token: int, audio: Any, key: str, raw: str, result: Any
    ) -> None:
        answer_fn = getattr(self.groq, "answer", None)
        if answer_fn is None:
            raise RuntimeError("answer mode unavailable")
        prior = ""
        try:
            prior = load_context()
        except Exception:
            prior = ""
        try:
            answered = answer_fn(raw, key, cancel=self._cancel, context=prior or None)
        except TypeError:
            answered = answer_fn(raw, key, cancel=self._cancel)
        final = answered.text
        if self._cancel.is_set() or token != self._token:
            return
        try:
            append_turn(raw, final)
        except Exception as exc:
            self.logger.warning(
                "event=memory_append_failed detail=%s", type(exc).__name__
            )
        show = getattr(self.feedback, "show_answer", None)
        if callable(show):
            show(raw, final)
        with self._lock:
            if self._cancel.is_set() or token != self._token:
                return
            self.history.insert(
                raw_text=raw,
                final_text=final,
                mode="assistant",
                detected_language=getattr(result, "language", None),
                delivery_status="displayed",
                cleanup_status="qa_answer",
                duration_ms=int(getattr(audio, "duration_seconds", 0) * 1000),
            )
            self.state = AppState.IDLE
            self._emit("assistant_complete")

    def _assistant_resume_or_clarify(
        self,
        token: int,
        audio: Any,
        raw: str,
        *,
        follow_up: str,
    ) -> None:
        """Resume a recent Hermes handoff, or ask which one when ambiguous."""
        recent = list_recent_handoffs(limit=5)
        if not recent:
            show = getattr(self.feedback, "show_answer", None)
            msg = (
                "No recent Vaani agent tasks to continue. "
                "Start a new one, or open a session in Hermes."
            )
            if callable(show):
                show(raw, msg)
            with self._lock:
                if self._cancel.is_set() or token != self._token:
                    return
                self.history.insert(
                    raw_text=raw,
                    final_text=msg,
                    mode="assistant",
                    delivery_status="displayed",
                    cleanup_status="resume_none",
                    duration_ms=int(getattr(audio, "duration_seconds", 0) * 1000),
                )
                self.state = AppState.IDLE
                self._emit("assistant_complete")
            return

        matched = match_handoff(follow_up if follow_up != raw else raw, recent)
        if matched is None and follow_up and follow_up != raw:
            matched = match_handoff(raw, recent)
        payload = follow_up.strip() if follow_up.strip() else (
            "Continue from where we left off. Give a short status update."
        )
        if matched is not None:
            self.logger.info(
                "event=assistant_session_resume session=%s chars=%s",
                matched.session_id,
                len(payload),
            )
            self._assistant_codex(
                token,
                audio,
                payload,
                confirmed=True,
                resume_session=matched.session_id,
            )
            return

        options = tuple(
            RouteOption(
                label=rec.title,
                intent="codex",
                query=payload,
                target=f"resume:{rec.session_id}",
            )
            for rec in recent
        ) + (
            RouteOption("New session (don't continue)", "codex", payload, "new"),
        )
        self.logger.info(
            "event=assistant_session_clarify options=%s", len(options)
        )
        self._begin_clarify(token, audio, raw, options)

    def _assistant_codex(
        self,
        token: int,
        audio: Any,
        raw: str,
        *,
        confirmed: bool = False,
        prompt: str | None = None,
        skill_id: str | None = None,
        resume_session: str | None = None,
    ) -> None:
        if self.codex is None:
            raise RuntimeError("assistant runner unavailable")
        agent_prompt = prompt if prompt is not None else raw
        # Hard gate: Hermes via Vaani is read-only (no confirm override).
        # Scan only the user utterance — skill markdown documents write CLIs
        # and must not trip the gate. Local open/play/media never reach here.
        if looks_like_agent_mutation(raw):
            self.logger.info(
                "event=agent_mutation_rejected chars=%s preview=%r",
                len(raw or ""),
                ((raw or "")[:80] + "…") if len(raw or "") > 80 else (raw or ""),
            )
            refuse = AGENT_MUTATION_REFUSE
            show = getattr(self.feedback, "show_answer", None)
            if callable(show):
                show(raw, refuse)
            elif self.result_window is not None and hasattr(
                self.result_window, "show_text"
            ):
                self.result_window.show_text(refuse)
            with self._lock:
                if self._cancel.is_set() or token != self._token:
                    return
                self.history.insert(
                    raw_text=raw,
                    final_text=refuse,
                    mode="assistant",
                    delivery_status="rejected",
                    cleanup_status="agent_read_only",
                    duration_ms=int(getattr(audio, "duration_seconds", 0) * 1000),
                )
                self.state = AppState.IDLE
                self._emit("cancelled")
            self._feedback("busy")
            return
        if not resume_session:
            agent_prompt = with_work_context_hint(agent_prompt, utterance=raw)
        # Provenance only — read-only rules live in the preloaded ``vaani`` skill.
        agent_prompt = with_vaani_source_tag(agent_prompt)
        if not confirmed:
            preview = " ".join((raw or "").split())
            if len(preview) > 120:
                preview = preview[:117] + "..."
            options = (
                RouteOption(f"Confirm — run assistant: {preview}", "codex", raw, "confirm"),
                RouteOption("Cancel", "paste", "", "cancel"),
            )
            self.logger.info("event=assistant_codex_confirm_prompt chars=%s", len(raw))
            self._begin_clarify(token, audio, raw, options)
            return
        self.logger.info(
            "event=assistant_route kind=codex chars=%s skill=%s resume=%s",
            len(raw),
            skill_id or "-",
            resume_session or "-",
        )
        self._feedback("processing")
        start_handoff = getattr(self.codex, "start_handoff", None)
        if not callable(start_handoff):
            # Legacy sync runner (tests / no handoff API).
            answer = self.codex.run(agent_prompt)
            if self.result_window is not None and hasattr(self.result_window, "show"):
                self.result_window.show(answer)
            if getattr(answer, "cancelled", False) or getattr(answer, "timed_out", False):
                raise RuntimeError("assistant request cancelled or timed out")
            final = answer.stdout
            with self._lock:
                if self._cancel.is_set() or token != self._token:
                    return
                self.history.insert(
                    raw_text=raw,
                    final_text=final,
                    mode="assistant",
                    delivery_status="displayed",
                    cleanup_status="skipped",
                    duration_ms=int(getattr(audio, "duration_seconds", 0) * 1000),
                )
                self.state = AppState.IDLE
                self._emit("assistant_complete")
                self._feedback("success")
            return

        from .hermes_notify import notify_handoff

        session_ref: dict[str, str | None] = {"id": None}
        released = threading.Event()

        def on_accepted(session_id: str | None) -> None:
            if session_id:
                session_ref["id"] = session_id
            with self._lock:
                if self._cancel.is_set() or token != self._token:
                    return
                self.history.insert(
                    raw_text=raw,
                    final_text=f"Handed to Hermes: {raw}",
                    mode="assistant",
                    delivery_status="displayed",
                    cleanup_status="agent_handoff",
                    duration_ms=int(getattr(audio, "duration_seconds", 0) * 1000),
                )
                self.state = AppState.IDLE
                self._handoff_job = None
                self._emit("assistant_complete")
                self._feedback("success")
            released.set()
            self.logger.info(
                "event=assistant_handoff_accepted chars=%s session=%s skill=%s",
                len(raw),
                session_ref["id"] or "-",
                skill_id or "-",
            )
            # Defer "started" notify until we know the Hermes session id so
            # Open jumps to this chat, not whatever was already focused.

        def on_complete(answer: Any) -> None:
            sid = getattr(answer, "session_id", None) or session_ref["id"]
            if sid:
                session_ref["id"] = sid
            if getattr(answer, "cancelled", False):
                kind = "cancelled"
                detail = None
            elif getattr(answer, "timed_out", False) or int(
                getattr(answer, "returncode", 0) or 0
            ):
                kind = "failed"
                detail = (getattr(answer, "stderr", None) or getattr(answer, "stdout", None) or "").strip()
            else:
                kind = "done"
                detail = (getattr(answer, "stdout", None) or "").strip()
            # If the job died before accept, release the pill here.
            if not released.is_set():
                with self._lock:
                    if token == self._token and self.state is AppState.PROCESSING:
                        self._handoff_job = None
                        self.state = AppState.IDLE
                        self._emit("cancelled" if kind == "cancelled" else "failure")
                        self._feedback("busy")
                released.set()
            self.logger.info(
                "event=assistant_handoff_complete kind=%s session=%s chars=%s",
                kind,
                sid or "-",
                len(detail or ""),
            )
            if kind == "cancelled":
                return
            notify_handoff(
                kind=kind,
                task=raw,
                session_ref=session_ref,
                detail=detail or None,
            )

        job = start_handoff(
            agent_prompt,
            on_accepted=on_accepted,
            on_complete=on_complete,
            skill_id=None if resume_session else skill_id,
            resume_session=resume_session,
        )
        with self._lock:
            self._handoff_job = job
        started_notified = threading.Event()
        remembered = threading.Event()

        def _remember(sid: str | None) -> None:
            if not sid or remembered.is_set():
                return
            remembered.set()
            try:
                # Store the user-facing task (not skill-wrapped prompt) for picker labels.
                remember_handoff(sid, raw)
            except Exception:
                self.logger.debug("handoff remember failed", exc_info=True)

        def _watch_session() -> None:
            for _ in range(240):
                sid = getattr(job, "session_id", None)
                if sid:
                    session_ref["id"] = sid
                    _remember(sid)
                    if released.is_set() and not started_notified.is_set():
                        started_notified.set()
                        notify_handoff(
                            kind="started",
                            task=raw,
                            session_ref=session_ref,
                        )
                    return
                if getattr(job, "_thread", None) is not None and not job._thread.is_alive():
                    sid = getattr(job, "session_id", None)
                    if sid:
                        session_ref["id"] = sid
                        _remember(sid)
                    if released.is_set() and not started_notified.is_set():
                        started_notified.set()
                        notify_handoff(
                            kind="started",
                            task=raw,
                            session_ref=session_ref,
                        )
                    return
                time.sleep(0.25)

        if resume_session:
            _remember(resume_session)
        threading.Thread(target=_watch_session, name="vaani-handoff-sid", daemon=True).start()

    def _assistant_open_site(
        self, token: int, audio: Any, raw: str, site: Any
    ) -> bool:
        self.logger.info(
            "event=assistant_route kind=browser site=%s",
            getattr(site, "name", "") or "blank",
        )
        url = site.url
        requested = site.browser if getattr(site, "browser", None) else None
        prefer = "chrome" if requested == "chrome" else "brave"
        if self.browser_launcher is not None:
            answer = self.browser_launcher.open(url, prefer=prefer)
        else:
            answer = self._open_browser(prefer_brave=prefer != "chrome", url=url)
        if answer.startswith("Opened"):
            answer = f"Opened {site.name}."
        if "youtube" in (url or "").casefold() or "youtu.be" in (url or "").casefold():
            try:
                from .play_library import record_play

                record_play(url=url, title=getattr(site, "name", "") or "", query=raw)
            except Exception:
                pass
        if self.result_window is not None and hasattr(self.result_window, "show_text"):
            self.result_window.show_text(answer)
        with self._lock:
            if self._cancel.is_set() or token != self._token:
                return True
            self.history.insert(
                raw_text=raw,
                final_text=answer,
                mode="assistant",
                delivery_status="displayed",
                cleanup_status="browser_action",
                duration_ms=int(getattr(audio, "duration_seconds", 0) * 1000),
            )
            self.state = AppState.IDLE
            self._emit("assistant_complete")
            self._feedback("success")
        return True

    def _assistant_try_volume(self, token: int, audio: Any, raw: str) -> bool:
        action = resolve_volume_action(raw)
        if action is None:
            return False
        self.logger.info("event=assistant_route kind=volume name=%s", action.name)
        answer = run_volume_action(action)
        if self.result_window is not None and hasattr(self.result_window, "show_text"):
            self.result_window.show_text(answer)
        with self._lock:
            if self._cancel.is_set() or token != self._token:
                return True
            self.history.insert(
                raw_text=raw,
                final_text=answer,
                mode="assistant",
                delivery_status="displayed",
                cleanup_status="volume_action",
                duration_ms=int(getattr(audio, "duration_seconds", 0) * 1000),
            )
            self.state = AppState.IDLE
            self._emit("assistant_complete")
            self._feedback("success")
        return True

    def _assistant_try_media(self, token: int, audio: Any, raw: str) -> bool:
        action = resolve_media_action(raw)
        if action is None:
            return False
        self.logger.info(
            "event=assistant_route kind=media name=%s times=%s",
            action.name,
            action.times,
        )
        answer = run_media_action(action, sender=self.media_keys)
        show = getattr(self.feedback, "show_answer", None)
        if callable(show):
            show(raw, answer)
        elif self.result_window is not None and hasattr(self.result_window, "show_text"):
            self.result_window.show_text(answer)
        with self._lock:
            if self._cancel.is_set() or token != self._token:
                return True
            self.history.insert(
                raw_text=raw,
                final_text=answer,
                mode="assistant",
                delivery_status="displayed",
                cleanup_status="media_action",
                duration_ms=int(getattr(audio, "duration_seconds", 0) * 1000),
            )
            self.state = AppState.IDLE
            self._emit("assistant_complete")
            self._feedback("success")
        return True

    def _assistant_try_app(self, token: int, audio: Any, raw: str) -> bool:
        app = (
            self.app_launcher.resolve(raw)
            if self.app_launcher is not None
            else resolve_app(raw)
        )
        if not app:
            return False
        self.logger.info("event=assistant_route kind=app name=%s", app.name)
        answer = (
            self.app_launcher.launch(app)
            if self.app_launcher is not None
            else launch_app(app)
        )
        if self.result_window is not None and hasattr(self.result_window, "show_text"):
            self.result_window.show_text(answer)
        with self._lock:
            if self._cancel.is_set() or token != self._token:
                return True
            self.history.insert(
                raw_text=raw,
                final_text=answer,
                mode="assistant",
                delivery_status="displayed",
                cleanup_status="app_action",
                duration_ms=int(getattr(audio, "duration_seconds", 0) * 1000),
            )
            self.state = AppState.IDLE
            self._emit("assistant_complete")
            self._feedback("success")
        return True

    def _assistant_try_folder(self, token: int, audio: Any, raw: str) -> bool:
        folder = resolve_folder(raw)
        if folder is None:
            return False
        self.logger.info("event=assistant_route kind=folder name=%s", folder.name)
        answer = launch_folder(folder)
        if self.result_window is not None and hasattr(self.result_window, "show_text"):
            self.result_window.show_text(answer)
        with self._lock:
            if self._cancel.is_set() or token != self._token:
                return True
            self.history.insert(
                raw_text=raw,
                final_text=answer,
                mode="assistant",
                delivery_status="displayed",
                cleanup_status="folder_action",
                duration_ms=int(getattr(audio, "duration_seconds", 0) * 1000),
            )
            self.state = AppState.IDLE
            self._emit("assistant_complete")
            self._feedback("success")
        return True

    def _assistant_try_browser(self, token: int, audio: Any, raw: str) -> bool:
        site = resolve_youtube(raw) or resolve_site(raw)
        browser = self._browser_intent(raw)
        if not site and not browser:
            return False
        if site:
            return self._assistant_open_site(token, audio, raw, site)
        self.logger.info("event=assistant_route kind=browser site=blank")
        prefer = "chrome" if "chrome" in raw.casefold() else "brave"
        if self.browser_launcher is not None:
            answer = self.browser_launcher.open("about:blank", prefer=prefer)
        else:
            answer = self._open_browser(prefer_brave=prefer != "chrome", url="about:blank")
        if self.result_window is not None and hasattr(self.result_window, "show_text"):
            self.result_window.show_text(answer)
        with self._lock:
            if self._cancel.is_set() or token != self._token:
                return True
            self.history.insert(
                raw_text=raw,
                final_text=answer,
                mode="assistant",
                delivery_status="displayed",
                cleanup_status="browser_action",
                duration_ms=int(getattr(audio, "duration_seconds", 0) * 1000),
            )
            self.state = AppState.IDLE
            self._emit("assistant_complete")
            self._feedback("success")
        return True

    def _assistant_try_skill(self, token: int, audio: Any, raw: str) -> bool:
        """Match a named skill and hand it to Hermes via the async handoff flow."""
        if self.codex is None:
            return False
        if not (
            hasattr(self.codex, "start_handoff") or hasattr(self.codex, "run_skill")
        ):
            return False
        skills = load_skill_index()
        skill = match_skill(raw, skills)
        if skill is None:
            return False
        mcp_list = list(skill.mcps)
        self.logger.info(
            "event=assistant_route kind=skill id=%s mcps=%s",
            skill.id,
            ",".join(mcp_list) or "-",
        )
        skill_prompt = build_skill_prompt(skill.body(), raw)
        # Same fire-and-forget path as "ask Vaani…" (notify + Hermes UI session).
        self._assistant_codex(
            token,
            audio,
            raw,
            confirmed=True,
            prompt=skill_prompt,
            skill_id=skill.id,
        )
        return True

    def _deliver_text(
        self,
        token: int,
        audio: Any,
        *,
        raw: str,
        final: str,
        history_mode: str,
        cleanup_status: str,
        language: str | None = None,
        emit_name: str = "delivered",
    ) -> None:
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
        status_value = getattr(status, "value", str(status))
        cue = "success"
        if status_value in {
            DeliveryStatus.FAILED.value,
            DeliveryStatus.FAILED,
            "failed",
        }:
            cue = "failure"
        elif status_value in {
            DeliveryStatus.CLIPBOARD_ONLY.value,
            DeliveryStatus.CLIPBOARD_ONLY,
            "clipboard_only",
        }:
            cue = "busy"
        with self._lock:
            if self._cancel.is_set() or token != self._token:
                return
            self.history.insert(
                raw_text=raw,
                final_text=final,
                mode=history_mode,
                detected_language=language,
                delivery_status=status_value,
                cleanup_status=cleanup_status,
                duration_ms=int(getattr(audio, "duration_seconds", 0) * 1000),
            )
            self.state = AppState.IDLE
            self._emit(emit_name)
            self._feedback(cue)

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
