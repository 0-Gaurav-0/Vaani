"""Agent runner v2 — pluggable brains, verb tools, ~10min sessions (T7.1)."""
from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from vaani.brains import BrainProtocol, ToolSpec, default_brains
from vaani.context import build_context
from vaani.exec.runner import run as exec_run
from vaani.intent.schema import (
    AgentSession,
    Context,
    Intent,
    Result,
    RiskClass,
    Status,
    Verb,
)
from vaani.intent.wake import parse_agent_wake
from vaani.observability import _redact
from vaani.platform.protocol import PlatformId
from vaani.policy.confirm import ConfirmEngine, requires_confirm
from vaani.policy.dryrun import materialize_argv
from vaani.verbs.registry import Registry

# Re-export for callers that import wake parsing from exec.agent.
__all__ = ["AgentResult", "AgentRunner", "parse_agent_wake"]

SESSION_TTL_SECONDS = 600.0  # ~10 minutes continuity
PROGRESS_WITHIN_SECONDS = 1.0
DEFAULT_TIMEOUT_SECONDS = 120.0


@dataclass(frozen=True)
class AgentResult:
    """Duck-compatible with CodexResult for ``agent.task`` handlers."""

    stdout: str
    stderr: str = ""
    returncode: int = 0
    timed_out: bool = False
    cancelled: bool = False
    brain: str = "codex"
    session_id: str | None = None
    tool_results: tuple[Result, ...] = ()


class AgentRunner:
    """Rung-6 runner: sessions, catalog tools, cancel/timeout/redaction."""

    def __init__(
        self,
        *,
        brains: Mapping[str, BrainProtocol] | None = None,
        registry: Registry | None = None,
        confirm: ConfirmEngine | None = None,
        platform: Callable[[], PlatformId] | None = None,
        get_context: Callable[[], Context] | None = None,
        default_brain: str = "codex",
        session_ttl: float = SESSION_TTL_SECONDS,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        progress_within: float = PROGRESS_WITHIN_SECONDS,
        clock: Callable[[], float] | None = None,
        on_progress: Callable[[str], None] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._brains: dict[str, BrainProtocol] = dict(brains or default_brains())
        self.registry = registry
        self.confirm = confirm
        self._platform = platform
        self._get_context = get_context
        if default_brain in self._brains:
            self.default_brain = default_brain
        elif "codex" in self._brains:
            self.default_brain = "codex"
        else:
            self.default_brain = next(iter(self._brains))
        self.session_ttl = max(1.0, float(session_ttl))
        self.timeout = max(1.0, float(timeout))
        self.progress_within = max(0.0, float(progress_within))
        self._clock = clock or time.monotonic
        self._on_progress = on_progress
        self._id_factory = id_factory or (lambda: secrets.token_hex(8))
        self._cancel = threading.Event()
        self._active: BrainProtocol | None = None
        self._lock = threading.RLock()
        self._session: AgentSession | None = None

    @property
    def session(self) -> AgentSession | None:
        with self._lock:
            return self._session

    def tools_for(self, platform: PlatformId) -> tuple[ToolSpec, ...]:
        """Expose the enabled verb catalog as the brain tool list."""
        if self.registry is None:
            return ()
        tools: list[ToolSpec] = []
        for verb in self.registry.enabled(platform):
            if verb.name == "agent.task":
                # Avoid recursive agent→agent tool loops.
                continue
            slots = {
                name: getattr(spec, "type", "str")
                for name, spec in verb.slots.items()
            }
            tools.append(
                ToolSpec(
                    name=verb.name,
                    title=verb.title,
                    slots=slots,
                    risk=verb.risk.value,
                    rung=verb.rung,
                )
            )
        return tuple(tools)

    def cancel(self) -> None:
        self._cancel.set()
        with self._lock:
            active = self._active
        if active is not None:
            try:
                active.cancel()
            except Exception:
                pass

    def run(
        self,
        prompt: str,
        *,
        brain: str | None = None,
        timeout: float | None = None,
        continue_session: bool | None = None,
    ) -> AgentResult:
        """Run one agent turn. Duck-compatible with ``CodexRunner.run``."""
        self._cancel.clear()
        name = self._resolve_brain(brain)
        adapter = self._brains.get(name) or self._brains[self.default_brain]
        name = adapter.name
        platform = self._platform() if self._platform is not None else PlatformId.LINUX
        tools = self.tools_for(platform)
        session_id = self._pick_session_id(
            name, continue_session=continue_session, prompt=prompt
        )
        progress_fired = threading.Event()

        def _progress(message: str) -> None:
            progress_fired.set()
            if self._on_progress is not None:
                try:
                    self._on_progress(_redact(message))
                except Exception:
                    pass

        def _ensure_progress() -> None:
            if self.progress_within <= 0:
                return
            time.sleep(self.progress_within)
            if not progress_fired.is_set() and not self._cancel.is_set():
                _progress(f"{name}: working…")

        watchdog = threading.Thread(target=_ensure_progress, daemon=True)
        watchdog.start()
        with self._lock:
            self._active = adapter
        try:
            result = adapter.run(
                prompt,
                tools=tools,
                session_id=session_id,
                cancel=self._cancel,
                on_progress=_progress,
                timeout=timeout if timeout is not None else self.timeout,
            )
        except Exception as exc:
            return AgentResult(
                stdout="",
                stderr=_redact(str(exc)),
                returncode=-1,
                brain=name,
                session_id=session_id,
            )
        finally:
            with self._lock:
                self._active = None

        tool_results: list[Result] = []
        if result.tool_calls:
            for call in result.tool_calls:
                tool_results.append(self.invoke_tool(call.name, dict(call.slots)))

        text = _redact(result.text or "")
        stderr = _redact(result.stderr or "")
        sid = result.session_id or session_id
        self._touch_session(sid, name)
        cancelled = result.cancelled or self._cancel.is_set()
        returncode = -1 if (result.timed_out or cancelled) else 0
        if tool_results and any(r.status is Status.REFUSED for r in tool_results):
            # Surface hard-rule refusals in stderr without failing the whole turn.
            refused = [
                r.summary for r in tool_results if r.status is Status.REFUSED
            ]
            if refused:
                stderr = _redact("; ".join(refused) + (("; " + stderr) if stderr else ""))
        return AgentResult(
            stdout=text,
            stderr=stderr,
            returncode=returncode,
            timed_out=result.timed_out,
            cancelled=cancelled,
            brain=name,
            session_id=sid,
            tool_results=tuple(tool_results),
        )

    def invoke_tool(
        self,
        name: str,
        slots: Mapping[str, Any] | None = None,
        *,
        via: str = "agent",
        context: Context | None = None,
    ) -> Result:
        """Dispatch a registered verb on behalf of a brain.

        R3/R4 cannot be approved ``via="agent"`` — ConfirmEngine rejects them.
        """
        if self.registry is None:
            return Result(
                status=Status.FAILED,
                summary="verb registry unavailable",
                detail="verb registry unavailable",
                rung=6,
            )
        verb = self.registry.get(name)
        if verb is None:
            return Result(
                status=Status.FAILED,
                summary=f"unknown verb {name}",
                detail=f"unknown verb {name}",
                rung=6,
            )
        ctx = context if context is not None else self._context()
        intent = Intent(
            verb=verb.name,
            slots=dict(slots or {}),
            rung=verb.rung,
            confidence=1.0,
            source="agent",
            mode="act",
            utterance=verb.name,
            raw_utterance=verb.name,
            modifiers=frozenset(),
            brain=None,
        )
        if requires_confirm(verb.risk):
            if self.confirm is None:
                return Result(
                    status=Status.REFUSED,
                    summary="confirmation required",
                    detail="confirmation engine unavailable for agent tool call",
                    rung=verb.rung,
                )
            materialized = self._materialize(verb, intent, ctx)
            pending = self.confirm.stage(
                intent, verb, materialized, context=ctx
            )
            approved = self.confirm.approve(pending.id, via=via)
            if approved is None:
                # Hard rule: brains cannot self-approve R3/R4.
                if verb.risk in {RiskClass.R3, RiskClass.R4} and via == "agent":
                    self.confirm.reject(pending.id)
                    return Result(
                        status=Status.REFUSED,
                        summary="agent cannot approve R3/R4",
                        detail=(
                            f"brain cannot self-approve {verb.risk.value} verb "
                            f"{verb.name}"
                        ),
                        rung=verb.rung,
                    )
                return Result(
                    status=Status.REFUSED,
                    summary="confirmation rejected",
                    detail=f"approve via={via!r} rejected for {verb.name}",
                    rung=verb.rung,
                )
            claimed = self.confirm.claim_execution()
            if claimed is None:
                return Result(
                    status=Status.FAILED,
                    summary="confirm claim failed",
                    detail="confirm claim failed",
                    rung=verb.rung,
                )
            intent, verb, ctx, _pending = claimed
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
        from vaani.policy.dryrun import dispatch

        return dispatch(verb, intent, ctx)

    def _resolve_brain(self, brain: str | None) -> str:
        if brain and brain.lower() in self._brains:
            return brain.lower()
        if self.default_brain in self._brains:
            return self.default_brain
        if "codex" in self._brains:
            return "codex"
        return next(iter(self._brains))

    def _pick_session_id(
        self,
        brain: str,
        *,
        continue_session: bool | None,
        prompt: str,
    ) -> str:
        _ = prompt  # topic-change spawn is a later refinement; TTL owns continuity.
        now = self._clock()
        with self._lock:
            current = self._session
            if current is None:
                return self._id_factory()
            expired = (now - current.last_active_at) >= self.session_ttl
            if expired or current.brain != brain:
                return self._id_factory()
            if continue_session is False:
                return self._id_factory()
            return current.id

    def _touch_session(self, session_id: str, brain: str) -> None:
        now = self._clock()
        with self._lock:
            started = now
            if self._session is not None and self._session.id == session_id:
                started = self._session.started_at
            self._session = AgentSession(
                id=session_id,
                brain=brain,
                started_at=started,
                last_active_at=now,
            )

    def _context(self) -> Context:
        if self._get_context is not None:
            return self._get_context()
        platform = (
            self._platform() if self._platform is not None else PlatformId.LINUX
        )
        return build_context(platform, runner=exec_run, include_focus=False)

    def _materialize(
        self, verb: Verb, intent: Intent, context: Context
    ) -> tuple[str, ...]:
        try:
            argv = materialize_argv(
                verb.name,
                dict(intent.slots),
                platform=context.platform,
                context=context,
            )
        except Exception:
            argv = None
        if argv:
            return tuple(str(p) for p in argv)
        return (verb.name, *[f"{k}={v}" for k, v in intent.slots.items()])

