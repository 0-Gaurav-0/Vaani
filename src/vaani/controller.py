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
from .groq import GroqError
from .apps import launch_app, resolve_app
from .sites import resolve_site
from .indicator_protocol import (
    clear_command,
    clear_options,
    clear_pending_id,
    parse_confirm_command,
    parse_select_command,
    read_command,
    resolve_options_path,
    resolve_pending_path,
    resolve_phase_path,
    write_options,
    write_pending_id,
    write_phase,
)
from .session_loop import SessionLoop
from .config import Settings
from .context import build_context
from .exec.runner import run as exec_run
from .exec.supervisor import Supervisor
from .intent.interrogative import refuse_interrogative, should_refuse_interrogative
from .intent.router import Router, prefer_verifiable_format
from .intent.schema import Context, Intent, Result, Status
from .platform import detect_os
from .policy.confirm import ConfirmEngine, requires_confirm
from .policy.disambiguate import (
    DISAMBIGUATE_PROBE_VERBS,
    PROBE_PACKS,
    DisambiguationEngine,
    merge_option_slots,
)
from .policy.dryrun import attach_workspace, dispatch, materialize_argv
from .policy.undo import UndoStack, register_undo
from .surface.result import format_result_message, show_result
from .verbs.packs.core import browser_intent, build_core_registry
from .verbs.packs.registry import PackRegistry, register_stub_packs

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
                 system: Any | None = None,
                 window: Any | None = None,
                 terminal: Any | None = None,
                 input_synth: Any | None = None,
                 supervisor: Any | None = None,
                 settings: Any | None = None,
                 vocab_path: str | os.PathLike[str] | None = None):
        self.recorder, self.groq, self.delivery, self.history = recorder, groq, delivery, history
        self.feedback, self.key_provider, self.hotkeys = feedback, key_provider or (lambda: None), hotkeys
        self._codex, self.result_window = codex, result_window
        self.agent = codex  # AgentRunner when wired; CodexRunner remains duck-compatible
        self.browser_launcher = browser_launcher
        self.app_launcher = app_launcher
        self.system = system
        self.window = window
        self.terminal = terminal
        self.input = input_synth
        if supervisor is not None:
            self.supervisor = supervisor
        else:
            resolved_settings = settings
            if resolved_settings is None:
                try:
                    resolved_settings = Settings.from_home()
                    resolved_settings.prepare()
                except Exception:
                    resolved_settings = None
            if resolved_settings is not None and all(
                hasattr(resolved_settings, attr)
                for attr in ("jobs_path", "log_dir", "data_dir")
            ):
                try:
                    self.supervisor = Supervisor(resolved_settings)
                except Exception:
                    self.supervisor = None
            else:
                self.supervisor = None
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
        cache_dir = Path(self.amplitude_path).parent
        self.indicator_phase_path = str(resolve_phase_path(cache_dir=cache_dir))
        self.indicator_pending_path = str(resolve_pending_path(cache_dir=cache_dir))
        self.indicator_options_path = str(resolve_options_path(cache_dir=cache_dir))
        self.confirm = ConfirmEngine()
        self.disambiguate = DisambiguationEngine()
        self.undo = UndoStack()
        self._session = SessionLoop(logger=self.logger)
        self._session.add("indicator_control", self._poll_indicator_control)
        self._session.add("amplitude", self._write_amplitude)
        self._session.add("confirm_expire", self._expire_confirm, every=0.25)
        self._session.add("disambiguate_expire", self._expire_disambiguate, every=0.25)
        self._session.start()
        self.registry, patterns = build_core_registry(
            resolve_app_fn=lambda text: resolve_app(text),
            launch_app_fn=lambda target: launch_app(target),
            resolve_site_fn=lambda text: resolve_site(text),
            open_browser_fn=lambda **kwargs: Controller._open_browser(**kwargs),
            get_app_launcher=lambda: self.app_launcher,
            get_browser_launcher=lambda: self.browser_launcher,
            get_codex=lambda: self.codex,
            get_result_window=lambda: self.result_window,
            get_system=lambda: self.system,
            get_window=lambda: self.window,
            get_delivery=lambda: self.delivery,
            get_platform=detect_os,
            get_supervisor=lambda: self.supervisor,
            get_terminal=lambda: self.terminal,
            get_cancel=lambda: self._cancel,
            run_command=exec_run,
        )
        patterns = patterns + register_undo(self.registry, self.undo)
        patterns = patterns + register_stub_packs(
            self.registry,
            run_fn=exec_run,
            get_platform=detect_os,
            get_input=lambda: self.input,
        )
        packs_settings = settings
        if packs_settings is None:
            try:
                packs_settings = Settings.from_home()
                packs_settings.prepare()
            except Exception:
                packs_settings = None
        if packs_settings is not None and hasattr(packs_settings, "packs_path"):
            PackRegistry(packs_settings.packs_path).apply(self.registry)
        self.router = Router(
            self.registry,
            patterns,
            resolve_app=lambda text: (
                self.app_launcher.resolve(text)
                if self.app_launcher is not None
                else resolve_app(text)
            ),
            resolve_site=lambda text: resolve_site(text),
            vocab_path=Path(vocab_path) if vocab_path is not None else None,
        )

    @property
    def codex(self) -> Any:
        return self._codex

    @codex.setter
    def codex(self, value: Any) -> None:
        self._codex = value
        self.agent = value

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
                self.logger.info("event=input_blocked reason=processing")
                return False
            if self.state is not AppState.IDLE:
                self._emit("busy")
                self._feedback("busy")
                return False
            # New utterance cancels a pending confirm (never approves).
            # Disambiguation waits for the transcript: ordinals select, else cancel.
            if self.confirm.peek() is not None:
                self._invalidate_pending(reason="utterance")
            self.mode = mode.value if isinstance(mode, DictationMode) else str(mode)
            self._cancel.clear(); self._token += 1; token = self._token
            try: self._audio = self.recorder.start()
            except Exception as exc: self._fail(exc, "mic"); return False
            self.state = AppState.RECORDING; self._record_started = time.monotonic(); self._emit("recording")
            try:
                clear_command(self.indicator_control_path)
            except Exception:
                pass
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
            if self.state is AppState.PROCESSING:
                self._emit("busy")
                self.logger.info("event=input_blocked reason=processing")
                return False
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
        # SessionLoop keeps polling indicator control during PROCESSING/IDLE.
        self._feedback("processing")
        try: audio = self.recorder.stop()
        except Exception as exc:
            self._fail(exc, "mic")
            return False
        worker = threading.Thread(target=self._process, args=(token, audio), daemon=True)
        self._worker = worker; worker.start(); return True

    def cancel(self) -> bool:
        """Cancel capture/processing, or reject pending confirm/disambiguate (Esc)."""
        prompt = self.disambiguate.peek()
        if prompt is not None and self.state is AppState.IDLE:
            return self.reject_pending(prompt.id, via="hotkey")
        pending = self.confirm.peek()
        if pending is not None and self.state is AppState.IDLE:
            return self.reject_pending(pending.id, via="hotkey")
        self._cancel.set()
        with self._lock:
            if self.state is AppState.RECORDING:
                try: self.recorder.cleanup()
                except Exception: pass
                self.state = AppState.IDLE; self._emit("cancelled"); self._feedback("busy"); return True
            if self.state is AppState.PROCESSING:
                self._token += 1
                try:
                    if self.mode == "assistant" and self.codex is not None:
                        self.codex.cancel()
                except Exception:
                    pass
                self.state = AppState.IDLE
                self._feedback("busy"); self._emit("cancelled"); return True
        return False

    def approve_pending(self, action_id: str | None = None, *, via: str = "hotkey") -> bool:
        """Approve the staged PendingAction (Enter / pill Approve)."""
        # Disambiguation never auto-approves — Enter is not a default pick.
        if self.disambiguate.peek() is not None:
            return False
        pending = self.confirm.peek()
        if pending is None:
            return False
        target = action_id or pending.id
        return self._handle_approve(target, via=via)

    def select_pending(self, index: int, action_id: str | None = None, *, via: str = "hotkey") -> bool:
        """Select a disambiguation option by 0-based index (voice / hotkey / pill)."""
        prompt = self.disambiguate.peek()
        if prompt is None:
            return False
        target = action_id or prompt.id
        chosen = self.disambiguate.select(target, index, via=via)
        if chosen is None:
            return False
        return self._handle_disambiguation_choice(via=via)

    def reject_pending(self, action_id: str | None = None, *, via: str = "hotkey") -> bool:
        """Reject staged confirm or disambiguation (Esc / pill Reject)."""
        prompt = self.disambiguate.peek()
        if prompt is not None:
            target = action_id or prompt.id
            rejected = self.disambiguate.reject(target)
            if rejected is None:
                return False
            self.logger.info(
                "event=disambiguate_rejected id=%s via=%s", rejected.id, via
            )
            self._emit("disambiguate_rejected")
            self._clear_confirm_ui()
            self._feedback("busy")
            return True
        pending = self.confirm.peek()
        if pending is None:
            return False
        target = action_id or pending.id
        rejected = self.confirm.reject(target)
        if rejected is None:
            return False
        self.logger.info("event=confirm_rejected id=%s via=%s", rejected.id, via)
        self._emit("confirm_rejected")
        self._clear_confirm_ui()
        self._feedback("busy")
        return True

    def _poll_indicator_control(self) -> None:
        """Honor stop/cancel/approve/reject/select requests from the floating pill."""
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
        select = parse_select_command(command)
        if select is not None:
            action_id, index = select
            self.select_pending(index, action_id, via="pill")
            return
        confirm = parse_confirm_command(command)
        if confirm is not None:
            kind, action_id = confirm
            if kind == "approve":
                self._handle_approve(action_id, via="pill")
            else:
                self.reject_pending(action_id, via="pill")
            return
        if command == "stop":
            self.stop()
        elif command == "cancel":
            self.cancel()

    def _write_amplitude(self) -> None:
        """Sample mic level while RECORDING; no-op in other states."""
        with self._lock:
            recording = self.state is AppState.RECORDING
            audio = self._audio
        if not recording or audio is None:
            return
        path = getattr(audio, "path", None)
        if path is None:
            return
        out = self.amplitude_path
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
                self._dispatch(raw, audio, token)
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
            if self._cancel.is_set():
                return
            try:
                self.recorder.cleanup()
            except Exception:
                pass
            self._fail(exc, getattr(exc, "category", None))

    def _dispatch(self, raw: str, audio: Any, token: int) -> None:
        """Route assistant speech through the verb registry and execute the hit."""
        # Pending disambiguation: ordinals select; anything else cancels then routes.
        if self.disambiguate.peek() is not None:
            if self.disambiguate.select_utterance(raw, via="voice") is not None:
                with self._lock:
                    if self._cancel.is_set() or token != self._token:
                        self.disambiguate.invalidate()
                        return
                    self.state = AppState.IDLE
                self._handle_disambiguation_choice(via="voice")
                return
            self._invalidate_disambiguation(reason="utterance")
        # A new transcript always invalidates any leftover pending confirm.
        if self.confirm.peek() is not None:
            self._invalidate_pending(reason="utterance")
        platform = detect_os()
        intent = self.router.route(raw, platform=platform)
        if intent is None:
            raise RuntimeError("assistant runner unavailable")
        context = build_context(platform, runner=exec_run)
        intent = prefer_verifiable_format(
            intent, context, self.registry, platform=platform
        )
        verb = self.registry.get(intent.verb)
        if verb is None:
            raise RuntimeError("assistant runner unavailable")
        # Interrogatives targeting mutating verbs: refuse before confirm/handler.
        # Guide/screen-offer is deferred (parallel-agents §0 / T2.5 override).
        if should_refuse_interrogative(verb, intent):
            result = attach_workspace(refuse_interrogative(verb, intent), context)
            self._surface_result(result)
            self._finish_assistant_result(
                result, raw=raw, audio=audio, verb_name=verb.name, token=token
            )
            return

        # Dry-run short-circuits before confirm staging or handler execution.
        if "dry_run" in intent.modifiers:
            result = dispatch(verb, intent, context)
            self._finish_assistant_result(
                result, raw=raw, audio=audio, verb_name=verb.name, token=token
            )
            return

        if requires_confirm(verb.risk):
            # Probe selected verbs so multi-match disambiguation wins before confirm.
            if verb.name in DISAMBIGUATE_PROBE_VERBS:
                probe = dispatch(verb, intent, context)
                if not self._absorb_policy_gate(
                    probe, intent=intent, verb=verb, context=context, token=token
                ):
                    if probe.status is Status.OK:
                        self.undo.record_success(verb, intent, probe)
                    self._finish_assistant_result(
                        probe,
                        raw=raw,
                        audio=audio,
                        verb_name=verb.name,
                        token=token,
                    )
                return

            materialized = self._materialize(intent, platform, context)
            pending = self.confirm.stage(
                intent, verb, materialized, context=context
            )
            result = attach_workspace(
                Result(
                    status=Status.NEEDS_CONFIRM,
                    summary=f"Confirm {verb.title}?",
                    detail=" ".join(pending.materialized),
                    evidence=pending.materialized,
                    rung=verb.rung,
                    pending=pending,
                ),
                context,
            )
            with self._lock:
                if self._cancel.is_set() or token != self._token:
                    self.confirm.invalidate()
                    return
                self.state = AppState.IDLE
                self._emit("needs_confirm")
            self._enter_confirming(pending, result)
            self.logger.info(
                "event=confirm_staged id=%s verb=%s risk=%s",
                pending.id,
                verb.name,
                verb.risk.value,
            )
            return

        result = dispatch(verb, intent, context)
        if result.status is Status.NEEDS_DISAMBIGUATE and result.disambiguation:
            with self._lock:
                if self._cancel.is_set() or token != self._token:
                    return
                self.state = AppState.IDLE
                self._emit("needs_disambiguate")
            self._enter_disambiguating(result.disambiguation, intent, verb, context, result)
            return
        # R0/R1 handlers may still stage confirm (slugify, dirty checkout, …).
        if result.status is Status.NEEDS_CONFIRM and result.pending is not None:
            staged_intent = Intent(
                verb=intent.verb,
                slots=dict(result.pending.slots),
                rung=intent.rung,
                confidence=intent.confidence,
                source=intent.source,
                mode=intent.mode,
                utterance=intent.utterance,
                raw_utterance=intent.raw_utterance,
                modifiers=intent.modifiers,
                brain=intent.brain,
            )
            pending = self.confirm.stage(
                staged_intent,
                verb,
                result.pending.materialized,
                context=context,
            )
            result = attach_workspace(
                Result(
                    status=Status.NEEDS_CONFIRM,
                    summary=result.summary,
                    detail=result.detail,
                    evidence=pending.materialized,
                    rung=verb.rung,
                    pending=pending,
                ),
                context,
            )
            with self._lock:
                if self._cancel.is_set() or token != self._token:
                    self.confirm.invalidate()
                    return
                self.state = AppState.IDLE
                self._emit("needs_confirm")
            self._enter_confirming(pending, result)
            self.logger.info(
                "event=confirm_staged id=%s verb=%s risk=%s via=handler",
                pending.id,
                verb.name,
                pending.risk.value,
            )
            return
        if result.status is Status.OK:
            self.undo.record_success(verb, intent, result)
        self._finish_assistant_result(
            result, raw=raw, audio=audio, verb_name=verb.name, token=token
        )

    def _handle_approve(self, action_id: str, *, via: str) -> bool:
        if self.disambiguate.peek() is not None:
            # Pill Approve must not default-pick a disambiguation option.
            return False
        approved = self.confirm.approve(action_id, via=via)
        if approved is None:
            self.logger.info(
                "event=confirm_approve_rejected id=%s via=%s", action_id, via
            )
            return False
        bundle = self.confirm.claim_execution()
        if bundle is None:
            return False
        intent, verb, context, pending = bundle
        # Packs that also gate on ``confirmed`` (e.g. procs) must see pill/Enter.
        intent = Intent(
            verb=intent.verb,
            slots=intent.slots,
            rung=intent.rung,
            confidence=intent.confidence,
            source=intent.source,
            mode=intent.mode,
            utterance=intent.utterance,
            raw_utterance=intent.raw_utterance,
            modifiers=frozenset(set(intent.modifiers) | {"confirmed"}),
            brain=intent.brain,
        )
        self.logger.info(
            "event=confirm_approved id=%s via=%s verb=%s",
            pending.id,
            via,
            verb.name,
        )
        self._emit("confirm_approved")
        self._clear_confirm_ui()
        with self._lock:
            if self.state is not AppState.IDLE:
                return False
            self.state = AppState.PROCESSING
            self._token += 1
            token = self._token
        try:
            result = dispatch(verb, intent, context)
            # Diff-before-apply / second-stage confirms (e.g. agent.task patch).
            if result.status in {
                Status.NEEDS_CONFIRM,
                Status.NEEDS_DISAMBIGUATE,
            } and self._absorb_policy_gate(
                result, intent=intent, verb=verb, context=context, token=token
            ):
                return True
            if result.status is Status.FAILED:
                raise RuntimeError(
                    result.detail or result.summary or "assistant failed"
                )
            if result.status is Status.OK:
                self.undo.record_success(verb, intent, result)
            self._finish_assistant_result(
                result,
                raw=intent.raw_utterance,
                verb_name=verb.name,
                duration_ms=0,
                token=token,
            )
        except Exception as exc:
            self._fail(exc, getattr(exc, "category", None))
        return True

    def _handle_disambiguation_choice(self, *, via: str) -> bool:
        bundle = self.disambiguate.claim_selection()
        if bundle is None:
            return False
        intent, verb, context, prompt, option = bundle
        slots = merge_option_slots(intent.slots, option)
        intent = Intent(
            verb=intent.verb,
            slots=slots,
            rung=intent.rung,
            confidence=intent.confidence,
            source=intent.source,
            mode=intent.mode,
            utterance=intent.utterance,
            raw_utterance=intent.raw_utterance,
            modifiers=intent.modifiers,
            brain=intent.brain,
        )
        self.logger.info(
            "event=disambiguate_selected id=%s via=%s verb=%s option=%s",
            prompt.id,
            via,
            verb.name,
            option.key,
        )
        self._emit("disambiguate_selected")
        self._clear_confirm_ui()
        with self._lock:
            if self.state is not AppState.IDLE:
                return False
            self.state = AppState.PROCESSING
            self._token += 1
            token = self._token
        try:
            if requires_confirm(verb.risk) and verb.pack in PROBE_PACKS:
                probe = dispatch(verb, intent, context)
                if self._absorb_policy_gate(
                    probe, intent=intent, verb=verb, context=context, token=token
                ):
                    return True
                if probe.status is Status.OK:
                    self.undo.record_success(verb, intent, probe)
                self._finish_assistant_result(
                    probe,
                    raw=intent.raw_utterance,
                    verb_name=verb.name,
                    duration_ms=0,
                    token=token,
                )
                return True
            if requires_confirm(verb.risk):
                materialized = self._materialize(intent, context.platform)
                pending = self.confirm.stage(
                    intent, verb, materialized, context=context
                )
                result = attach_workspace(
                    Result(
                        status=Status.NEEDS_CONFIRM,
                        summary=f"Confirm {verb.title}?",
                        detail=" ".join(pending.materialized),
                        evidence=pending.materialized,
                        rung=verb.rung,
                        pending=pending,
                    ),
                    context,
                )
                with self._lock:
                    self.state = AppState.IDLE
                    self._emit("needs_confirm")
                self._enter_confirming(pending, result)
                return True
            result = dispatch(verb, intent, context)
            if result.status is Status.OK:
                self.undo.record_success(verb, intent, result)
            self._finish_assistant_result(
                result,
                raw=intent.raw_utterance,
                verb_name=verb.name,
                duration_ms=0,
                token=token,
            )
        except Exception as exc:
            self._fail(exc, getattr(exc, "category", None))
        return True

    def _absorb_policy_gate(
        self,
        result: Result,
        *,
        intent: Intent,
        verb: Any,
        context: Context,
        token: int,
    ) -> bool:
        """Stage confirm/disambiguate UI when a probe Result requests it.

        Returns True when the result was absorbed (caller should stop).
        """
        if result.status is Status.NEEDS_DISAMBIGUATE and result.disambiguation:
            with self._lock:
                if self._cancel.is_set() or token != self._token:
                    return True
                self.state = AppState.IDLE
                self._emit("needs_disambiguate")
            self._enter_disambiguating(
                result.disambiguation, intent, verb, context, result
            )
            return True
        if result.status is Status.NEEDS_CONFIRM:
            materialized = (
                result.pending.materialized
                if result.pending is not None
                else self._materialize(intent, context.platform)
            )
            # Prefer handler-provided slots (seeded prompt / proposed_diff).
            staged_slots = (
                dict(result.pending.slots)
                if result.pending is not None
                else dict(intent.slots)
            )
            staged_intent = Intent(
                verb=intent.verb,
                slots=staged_slots,
                rung=intent.rung,
                confidence=intent.confidence,
                source=intent.source,
                mode=intent.mode,
                utterance=intent.utterance,
                raw_utterance=intent.raw_utterance,
                modifiers=intent.modifiers,
                brain=intent.brain,
            )
            pending = self.confirm.stage(
                staged_intent, verb, materialized, context=context
            )
            gated = attach_workspace(
                Result(
                    status=Status.NEEDS_CONFIRM,
                    summary=result.summary or f"Confirm {verb.title}?",
                    detail=result.detail or " ".join(pending.materialized),
                    evidence=pending.materialized,
                    rung=verb.rung,
                    pending=pending,
                ),
                context,
            )
            with self._lock:
                if self._cancel.is_set() or token != self._token:
                    self.confirm.invalidate()
                    return True
                self.state = AppState.IDLE
                self._emit("needs_confirm")
            self._enter_confirming(pending, gated)
            self.logger.info(
                "event=confirm_staged id=%s verb=%s risk=%s",
                pending.id,
                verb.name,
                verb.risk.value,
            )
            return True
        if result.status in {
            Status.REFUSED,
            Status.FAILED,
            Status.UNSUPPORTED,
            Status.PARTIAL,
            Status.DRY_RUN,
        }:
            with self._lock:
                if self._cancel.is_set() or token != self._token:
                    return True
            self._finish_assistant_result(
                result,
                raw=intent.raw_utterance,
                verb_name=verb.name,
                duration_ms=0,
                token=token,
            )
            return True
        return False

    def _finish_assistant_result(
        self,
        result: Result,
        *,
        raw: str,
        audio: Any | None = None,
        verb_name: str = "",
        duration_ms: int | None = None,
        token: int,
    ) -> None:
        if result.status is Status.FAILED:
            raise RuntimeError(result.detail or result.summary or "assistant failed")
        final = result.detail or result.summary
        name = verb_name
        if not name:
            try:
                routed = self.router.route(raw, platform=detect_os())
                if routed is not None:
                    name = routed.verb
            except Exception:
                name = ""
        cleanup = {
            "app.open": "app_action",
            "site.open": "browser_action",
            "browser.open": "browser_action",
        }.get(name, "skipped")
        if duration_ms is None:
            duration_ms = int(getattr(audio, "duration_seconds", 0) * 1000)
        with self._lock:
            if self._cancel.is_set() or token != self._token:
                return
            self.history.insert(
                raw_text=raw,
                final_text=final,
                mode="assistant",
                delivery_status="displayed",
                cleanup_status=cleanup,
                duration_ms=duration_ms,
            )
            self.state = AppState.IDLE
            self._emit("assistant_complete")
            self._feedback("success")

    def _materialize(
        self, intent: Any, platform: Any, context: Any = None
    ) -> tuple[str, ...]:
        try:
            argv = materialize_argv(
                intent.verb,
                dict(intent.slots),
                platform=platform,
                context=context,
            )
        except Exception:
            argv = None
        if argv is None:
            argv = (
                intent.verb,
                *[f"{k}={intent.slots[k]}" for k in sorted(intent.slots)],
            )
        return tuple(argv)

    def _surface_result(self, result: Result) -> None:
        """Show a Result on feedback / result window (no confirm UI)."""
        message = format_result_message(result)
        if self.feedback is not None:
            try:
                show_result(self.feedback, result)
            except Exception:
                pass
        if self.result_window is not None and hasattr(self.result_window, "show_text"):
            try:
                self.result_window.show_text(message)
            except Exception:
                pass

    def _enter_confirming(self, pending: Any, result: Result) -> None:
        try:
            write_pending_id(self.indicator_pending_path, pending.id)
        except Exception:
            pass
        try:
            clear_options(self.indicator_options_path)
        except Exception:
            pass
        setter = getattr(self.feedback, "set_pending_id", None)
        if callable(setter):
            try:
                setter(pending.id)
            except Exception:
                pass
        self._feedback("confirming")
        try:
            write_phase(self.indicator_phase_path, "confirming")
        except Exception:
            pass
        self._surface_result(result)

    def _enter_disambiguating(
        self,
        prompt: Any,
        intent: Intent,
        verb: Any,
        context: Context,
        result: Result,
    ) -> None:
        staged = self.disambiguate.stage(
            intent, verb, prompt, context=context
        )
        try:
            write_pending_id(self.indicator_pending_path, staged.id)
        except Exception:
            pass
        try:
            write_options(
                self.indicator_options_path,
                [opt.label for opt in staged.options],
            )
        except Exception:
            pass
        setter = getattr(self.feedback, "set_pending_id", None)
        if callable(setter):
            try:
                setter(staged.id)
            except Exception:
                pass
        self._feedback("confirming")
        try:
            write_phase(self.indicator_phase_path, "confirming")
        except Exception:
            pass
        # Prefer the engine-staged prompt on the surfaced Result.
        surfaced = attach_workspace(
            Result(
                status=Status.NEEDS_DISAMBIGUATE,
                summary=result.summary,
                detail=result.detail,
                evidence=result.evidence,
                rung=result.rung,
                disambiguation=staged,
            ),
            context,
        )
        self._surface_result(surfaced)
        self.logger.info(
            "event=disambiguate_staged id=%s verb=%s options=%s",
            staged.id,
            verb.name,
            len(staged.options),
        )

    def _clear_confirm_ui(self) -> None:
        try:
            clear_pending_id(self.indicator_pending_path)
        except Exception:
            pass
        try:
            clear_options(self.indicator_options_path)
        except Exception:
            pass
        setter = getattr(self.feedback, "set_pending_id", None)
        if callable(setter):
            try:
                setter(None)
            except Exception:
                pass

    def _invalidate_pending(self, *, reason: str) -> None:
        rejected = self.confirm.invalidate()
        if rejected is None:
            return
        self.logger.info(
            "event=confirm_invalidated id=%s reason=%s", rejected.id, reason
        )
        self._emit("confirm_invalidated")
        self._clear_confirm_ui()

    def _invalidate_disambiguation(self, *, reason: str) -> None:
        rejected = self.disambiguate.invalidate()
        if rejected is None:
            return
        self.logger.info(
            "event=disambiguate_invalidated id=%s reason=%s", rejected.id, reason
        )
        self._emit("disambiguate_invalidated")
        self._clear_confirm_ui()

    def _expire_confirm(self) -> None:
        expired = self.confirm.expire_tick()
        if expired is None:
            return
        self.logger.info("event=confirm_expired id=%s", expired.id)
        self._emit("confirm_expired")
        self._clear_confirm_ui()
        self._feedback("busy")

    def _expire_disambiguate(self) -> None:
        """TTL cancel — never selects a default option."""
        expired = self.disambiguate.expire_tick()
        if expired is None:
            return
        self.logger.info("event=disambiguate_expired id=%s", expired.id)
        self._emit("disambiguate_expired")
        self._clear_confirm_ui()
        self._feedback("busy")

    @staticmethod
    def _browser_intent(text: str) -> bool:
        """Compat shim for existing unit tests — routing uses the grammar allowlist."""
        return browser_intent(text)

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
        self._session.stop()
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
