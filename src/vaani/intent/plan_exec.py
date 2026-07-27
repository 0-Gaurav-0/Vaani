"""Multi-step plan execution with confirm policy B.

Policy B (design §4.4):
- Auto-run R0/R1 steps.
- Pause on the first R2+ step; stash remaining steps after it.
- After approve, dispatch the confirmed step, then continue with the same rules.
- On reject / ``clear()``, drop remaining steps.
- On failure, stop; use ``Status.PARTIAL`` when earlier steps succeeded.
"""
from __future__ import annotations

import logging
import secrets
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

from vaani.intent.schema import (
    Context,
    Intent,
    IntentPlan,
    PendingAction,
    PlanStep,
    Result,
    RiskClass,
    Status,
    Verb,
)
from vaani.policy.confirm import DEFAULT_TTL_SECONDS, requires_confirm
from vaani.verbs.registry import Registry

DispatchFn = Callable[[Verb, Intent, Context], Result]
RiskCheckFn = Callable[[RiskClass], bool]
MaterializeFn = Callable[[PlanStep, Verb, Context], tuple[str, ...]]

_logger = logging.getLogger(__name__)

_BROWSER_APP_KEYS: dict[str, str] = {
    "google chrome": "chrome",
    "chrome": "chrome",
    "brave browser": "brave",
    "brave": "brave",
}


def coalesce_browser_open_search(
    steps: Sequence[PlanStep],
) -> tuple[PlanStep, ...]:
    """Fold ``app.open`` Chrome/Brave + ``site.search`` into one search in that browser.

    Opening the app then immediately opening a URL races Chrome's launch and
    often leaves the user staring at an empty window while the search tab is
    easy to miss.
    """
    if len(steps) < 2:
        return tuple(steps)
    out: list[PlanStep] = []
    index = 0
    while index < len(steps):
        current = steps[index]
        nxt = steps[index + 1] if index + 1 < len(steps) else None
        browser = _browser_key_from_app_open(current)
        if browser and nxt is not None and nxt.verb == "site.search":
            slots = dict(nxt.slots)
            slots.setdefault("browser", browser)
            out.append(PlanStep(verb="site.search", slots=slots, note=nxt.note))
            index += 2
            continue
        out.append(current)
        index += 1
    return tuple(out)


def _browser_key_from_app_open(step: PlanStep) -> str | None:
    if step.verb != "app.open":
        return None
    name = str(step.slots.get("name") or "").strip().casefold()
    return _BROWSER_APP_KEYS.get(name)


@dataclass
class PlanExecState:
    """Side-channel for remaining plan steps after a confirm pause."""

    remaining: tuple[PlanStep, ...] = ()
    raw_utterance: str = ""
    utterance: str = ""
    source: str = "llm"
    confidence: float = 1.0


def pending_from_step(
    step: PlanStep,
    verb: Verb,
    *,
    expires_at: float,
    id: str | None = None,
    materialized: tuple[str, ...] | None = None,
) -> PendingAction:
    """Build a PendingAction for a plan step (Task 8 wires ConfirmEngine)."""
    argv = materialized if materialized is not None else _default_materialize(step)
    return PendingAction(
        id=id or secrets.token_hex(8),
        verb=verb.name,
        slots=dict(step.slots),
        materialized=argv,
        risk=verb.risk,
        expires_at=expires_at,
    )


def format_confirm_detail(
    step: PlanStep,
    remaining: Sequence[PlanStep],
    *,
    materialized: Sequence[str] | None = None,
) -> str:
    """Confirm detail for the current step, plus ``Then: …`` for upcoming steps."""
    base = " ".join(str(p) for p in materialized) if materialized else step.verb
    if step.note:
        base = f"{base} ({step.note})" if base else step.note
    if not remaining:
        return base
    then_parts = [s.note or s.verb for s in remaining]
    then = ", ".join(then_parts)
    if base:
        return f"{base}\nThen: {then}"
    return f"Then: {then}"


def _default_materialize(step: PlanStep) -> tuple[str, ...]:
    parts = [step.verb]
    for key in sorted(step.slots):
        value = step.slots[key]
        if isinstance(value, (str, int, float, bool)):
            parts.append(f"{key}={value}")
    return tuple(parts)


class PlanExecutor:
    """Pure-ish walker over ``IntentPlan`` steps with confirm policy B."""

    def __init__(
        self,
        registry: Registry,
        *,
        dispatch: DispatchFn,
        risk_requires_confirm: RiskCheckFn | None = None,
        materialize: MaterializeFn | None = None,
        clock: Callable[[], float] | None = None,
        id_factory: Callable[[], str] | None = None,
        ttl: float = DEFAULT_TTL_SECONDS,
    ) -> None:
        self.registry = registry
        self._dispatch = dispatch
        self._risk_requires_confirm = risk_requires_confirm or requires_confirm
        self._materialize = materialize
        self._clock = clock or time.monotonic
        self._id_factory = id_factory or (lambda: secrets.token_hex(8))
        self._ttl = max(0.1, float(ttl))
        self.state = PlanExecState()

    def clear(self) -> None:
        """Drop any stashed remaining steps (reject / expire / new utterance)."""
        self.state = PlanExecState()

    def start(self, plan: IntentPlan, context: Context) -> Result:
        """Run policy B from the start of ``plan``."""
        self.state = PlanExecState(
            remaining=(),
            raw_utterance=plan.raw_utterance,
            utterance=plan.utterance,
            source=plan.source,
            confidence=plan.confidence,
        )
        if not plan.steps:
            return Result(
                status=Status.FAILED,
                summary="Empty plan",
                detail=plan.refuse_reason or plan.delegate_prompt or "",
            )
        steps = coalesce_browser_open_search(plan.steps)
        if steps != plan.steps:
            _logger.info(
                "event=llm_parse_stage stage=execute status=coalesce "
                "from=%s to=%s",
                len(plan.steps),
                len(steps),
            )
        step_summary = " -> ".join(
            f"{s.verb}({','.join(f'{k}={v!r}' for k, v in s.slots.items())})"
            for s in steps
        )
        _logger.info(
            "event=llm_parse_stage stage=execute status=start steps=%s plan=%s utterance=%r",
            len(steps),
            step_summary,
            (plan.raw_utterance or plan.utterance)[:120],
        )
        return self._run_steps(steps, context, ok_count=0, summaries=[])

    def continue_after_confirm(self, intent: Intent, context: Context) -> Result:
        """Dispatch an already-confirmed R2+ intent, then continue remaining steps."""
        verb = self.registry.get(intent.verb)
        if verb is None:
            self.clear()
            return Result(
                status=Status.FAILED,
                summary=f"Unknown verb: {intent.verb}",
                rung=intent.rung,
            )

        # Preserve utterance metadata from the confirmed intent if state was cleared.
        if not self.state.utterance and intent.utterance:
            self.state = replace(
                self.state,
                utterance=intent.utterance,
                raw_utterance=intent.raw_utterance or intent.utterance,
                source=intent.source or self.state.source,
                confidence=intent.confidence if intent.confidence else self.state.confidence,
            )

        result = self._dispatch(verb, intent, context)
        if result.status is not Status.OK and result.status is not Status.DRY_RUN:
            self.clear()
            return result

        summaries = [result.summary] if result.summary else []
        remaining = self.state.remaining
        self.state = replace(self.state, remaining=())
        if not remaining:
            return Result(
                status=Status.OK,
                summary=_join_summaries(summaries) or result.summary,
                detail=result.detail,
                evidence=result.evidence,
                rung=verb.rung,
                undo=result.undo,
            )
        return self._run_steps(remaining, context, ok_count=1, summaries=summaries)

    def _run_steps(
        self,
        steps: Sequence[PlanStep],
        context: Context,
        *,
        ok_count: int,
        summaries: list[str],
    ) -> Result:
        last_ok: Result | None = None
        for index, step in enumerate(steps):
            verb = self.registry.get(step.verb)
            if verb is None:
                return self._stop_failure(
                    f"Unknown verb: {step.verb}",
                    ok_count=ok_count,
                    summaries=summaries,
                    detail=step.verb,
                )

            intent = self._intent_for_step(step, verb)
            _logger.info(
                "event=llm_parse_stage stage=execute status=step index=%s verb=%s slots=%s",
                index,
                step.verb,
                dict(step.slots),
            )

            if self._risk_requires_confirm(verb.risk):
                rest = tuple(steps[index + 1 :])
                self.state = replace(self.state, remaining=rest)
                materialized = self._materialize_step(step, verb, context)
                pending = pending_from_step(
                    step,
                    verb,
                    expires_at=self._clock() + self._ttl,
                    id=self._id_factory(),
                    materialized=materialized,
                )
                detail = format_confirm_detail(
                    step, rest, materialized=pending.materialized
                )
                _logger.info(
                    "event=llm_parse_stage stage=execute status=needs_confirm verb=%s rest=%s",
                    step.verb,
                    len(rest),
                )
                return Result(
                    status=Status.NEEDS_CONFIRM,
                    summary=f"Confirm {verb.title}?",
                    detail=detail,
                    evidence=pending.materialized,
                    rung=verb.rung,
                    pending=pending,
                )

            result = self._dispatch(verb, intent, context)
            _logger.info(
                "event=llm_parse_stage stage=execute status=done index=%s verb=%s result=%s summary=%r",
                index,
                step.verb,
                result.status.value,
                (result.summary or "")[:80],
            )
            if result.status is Status.OK or result.status is Status.DRY_RUN:
                ok_count += 1
                if result.summary:
                    summaries.append(result.summary)
                last_ok = result
                continue

            # Handler-staged confirm on an R0/R1 path: surface it and stash rest.
            if result.status is Status.NEEDS_CONFIRM and result.pending is not None:
                rest = tuple(steps[index + 1 :])
                self.state = replace(self.state, remaining=rest)
                detail = result.detail
                if rest:
                    then_only = "Then: " + ", ".join(s.note or s.verb for s in rest)
                    detail = f"{detail}\n{then_only}".strip() if detail else then_only
                return replace(result, detail=detail)

            self.clear()
            if ok_count > 0 and result.status in {
                Status.FAILED,
                Status.UNSUPPORTED,
                Status.REFUSED,
            }:
                return replace(
                    result,
                    status=Status.PARTIAL,
                    summary=_join_summaries(summaries + [result.summary])
                    or result.summary,
                )
            return result

        summary = _join_summaries(summaries)
        if last_ok is not None:
            return Result(
                status=Status.OK,
                summary=summary or last_ok.summary,
                detail=last_ok.detail,
                evidence=last_ok.evidence,
                rung=last_ok.rung,
                undo=last_ok.undo,
            )
        return Result(status=Status.OK, summary=summary or "Done")

    def _intent_for_step(self, step: PlanStep, verb: Verb) -> Intent:
        return Intent(
            verb=verb.name,
            slots=dict(step.slots),
            rung=verb.rung,
            confidence=self.state.confidence,
            source=self.state.source or "llm",
            mode="act",
            utterance=self.state.utterance,
            raw_utterance=self.state.raw_utterance,
            modifiers=frozenset(),
            brain=None,
        )

    def _materialize_step(
        self, step: PlanStep, verb: Verb, context: Context
    ) -> tuple[str, ...]:
        if self._materialize is not None:
            return self._materialize(step, verb, context)
        return _default_materialize(step)

    def _stop_failure(
        self,
        summary: str,
        *,
        ok_count: int,
        summaries: list[str],
        detail: str = "",
        rung: int = 0,
    ) -> Result:
        self.clear()
        if ok_count > 0:
            return Result(
                status=Status.PARTIAL,
                summary=_join_summaries(summaries + [summary]) or summary,
                detail=detail,
                rung=rung,
            )
        return Result(status=Status.FAILED, summary=summary, detail=detail, rung=rung)


def _join_summaries(parts: Sequence[str]) -> str:
    cleaned = [p.strip() for p in parts if p and p.strip()]
    if not cleaned:
        return ""
    return "; ".join(cleaned)
