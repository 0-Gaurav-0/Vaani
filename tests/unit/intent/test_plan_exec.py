"""Unit tests for PlanExecutor confirm policy B."""
from __future__ import annotations

from vaani.intent.plan_exec import PlanExecutor, format_confirm_detail, pending_from_step
from vaani.intent.schema import (
    Context,
    Intent,
    IntentPlan,
    PlanStep,
    Result,
    RiskClass,
    SlotSpec,
    Status,
    Support,
    Verb,
)
from vaani.platform.protocol import PlatformId
from vaani.policy.confirm import requires_confirm
from vaani.verbs.registry import Registry


def _context() -> Context:
    return Context(
        platform=PlatformId.MACOS,
        workspace=None,
        workspace_source="home",
        repo=None,
        project=None,
        focus=None,
        screen=None,
        session=None,
    )


def _verb(name: str, *, risk: RiskClass, title: str | None = None) -> Verb:
    def handler(intent: Intent, _context: Context) -> Result:
        return Result(status=Status.OK, summary=f"ran {intent.verb}", rung=1)

    return Verb(
        name=name,
        title=title or name,
        slots={"name": SlotSpec(type="str", required=False)},
        rung=1 if risk in {RiskClass.R0, RiskClass.R1} else 3,
        risk=risk,
        requires=frozenset(),
        support={
            PlatformId.LINUX: Support.SUPPORTED,
            PlatformId.MACOS: Support.SUPPORTED,
            PlatformId.WINDOWS: Support.SUPPORTED,
        },
        undo=None,
        pack="core",
        handler=handler,
    )


def _plan(*steps: PlanStep, utterance: str = "do things") -> IntentPlan:
    return IntentPlan(
        steps=steps,
        utterance=utterance,
        raw_utterance=utterance,
        source="llm",
        confidence=0.9,
    )


def _executor(
    registry: Registry,
    *,
    calls: list[tuple[str, frozenset[str]]] | None = None,
) -> PlanExecutor:
    recorded = calls if calls is not None else []

    def dispatch(verb: Verb, intent: Intent, context: Context) -> Result:
        recorded.append((intent.verb, intent.modifiers))
        return verb.handler(intent, context)

    return PlanExecutor(
        registry,
        dispatch=dispatch,
        risk_requires_confirm=requires_confirm,
        clock=lambda: 1000.0,
        id_factory=lambda: "pending-1",
    )


def test_two_r0_steps_both_dispatched_ok() -> None:
    registry = Registry()
    registry.register(_verb("app.open", risk=RiskClass.R0, title="Open app"))
    registry.register(_verb("site.search", risk=RiskClass.R0, title="Search"))
    calls: list[tuple[str, frozenset[str]]] = []
    executor = _executor(registry, calls=calls)

    result = executor.start(
        _plan(
            PlanStep(verb="app.open", slots={"name": "Brave"}),
            PlanStep(verb="site.search", slots={"name": "Zepter"}),
        ),
        _context(),
    )

    assert result.status is Status.OK
    assert [c[0] for c in calls] == ["app.open", "site.search"]
    assert executor.state.remaining == ()
    assert result.pending is None


def test_r0_then_r2_pauses_with_remaining() -> None:
    registry = Registry()
    registry.register(_verb("app.open", risk=RiskClass.R0, title="Open app"))
    registry.register(_verb("app.quit", risk=RiskClass.R2, title="Quit app"))
    registry.register(_verb("site.search", risk=RiskClass.R0, title="Search"))
    calls: list[tuple[str, frozenset[str]]] = []
    executor = _executor(registry, calls=calls)

    result = executor.start(
        _plan(
            PlanStep(verb="app.open", slots={"name": "Brave"}),
            PlanStep(verb="app.quit", slots={"name": "Slack"}),
            PlanStep(verb="site.search", slots={"name": "docs"}),
        ),
        _context(),
    )

    assert result.status is Status.NEEDS_CONFIRM
    assert result.pending is not None
    assert result.pending.verb == "app.quit"
    assert result.pending.id == "pending-1"
    assert [c[0] for c in calls] == ["app.open"]
    assert executor.state.remaining == (
        PlanStep(verb="site.search", slots={"name": "docs"}),
    )
    assert "Then:" in result.detail
    assert "site.search" in result.detail


def test_continue_after_confirm_runs_r2_and_trailing_r0() -> None:
    registry = Registry()
    registry.register(_verb("app.open", risk=RiskClass.R0, title="Open app"))
    registry.register(_verb("app.quit", risk=RiskClass.R2, title="Quit app"))
    registry.register(_verb("site.search", risk=RiskClass.R0, title="Search"))
    calls: list[tuple[str, frozenset[str]]] = []
    executor = _executor(registry, calls=calls)

    start = executor.start(
        _plan(
            PlanStep(verb="app.open", slots={"name": "Brave"}),
            PlanStep(verb="app.quit", slots={"name": "Slack"}),
            PlanStep(verb="site.search", slots={"name": "docs"}),
        ),
        _context(),
    )
    assert start.status is Status.NEEDS_CONFIRM
    assert start.pending is not None

    confirmed = Intent(
        verb="app.quit",
        slots={"name": "Slack"},
        rung=3,
        confidence=0.9,
        source="llm",
        mode="act",
        utterance="do things",
        raw_utterance="do things",
        modifiers=frozenset({"confirmed"}),
        brain=None,
    )
    result = executor.continue_after_confirm(confirmed, _context())

    assert result.status is Status.OK
    assert [c[0] for c in calls] == ["app.open", "app.quit", "site.search"]
    assert calls[1][1] == frozenset({"confirmed"})
    assert executor.state.remaining == ()


def test_clear_drops_remaining_reject_path() -> None:
    registry = Registry()
    registry.register(_verb("app.open", risk=RiskClass.R0))
    registry.register(_verb("app.quit", risk=RiskClass.R2, title="Quit app"))
    registry.register(_verb("site.search", risk=RiskClass.R0))
    executor = _executor(registry)

    result = executor.start(
        _plan(
            PlanStep(verb="app.open", slots={"name": "Brave"}),
            PlanStep(verb="app.quit", slots={"name": "Slack"}),
            PlanStep(verb="site.search", slots={"name": "docs"}),
        ),
        _context(),
    )
    assert result.status is Status.NEEDS_CONFIRM
    assert len(executor.state.remaining) == 1

    executor.clear()
    assert executor.state.remaining == ()
    assert executor.state.utterance == ""


def test_missing_verb_fails() -> None:
    registry = Registry()
    registry.register(_verb("app.open", risk=RiskClass.R0))
    calls: list[tuple[str, frozenset[str]]] = []
    executor = _executor(registry, calls=calls)

    result = executor.start(
        _plan(
            PlanStep(verb="app.open", slots={"name": "Brave"}),
            PlanStep(verb="no.such", slots={}),
        ),
        _context(),
    )

    assert result.status is Status.PARTIAL
    assert [c[0] for c in calls] == ["app.open"]
    assert "Unknown verb" in result.summary


def test_pending_from_step_and_confirm_detail_helpers() -> None:
    verb = _verb("app.quit", risk=RiskClass.R2, title="Quit app")
    step = PlanStep(verb="app.quit", slots={"name": "Slack"}, note="quit Slack")
    pending = pending_from_step(
        step,
        verb,
        expires_at=42.0,
        id="abc",
        materialized=("quit", "Slack"),
    )
    assert pending.id == "abc"
    assert pending.risk is RiskClass.R2
    assert pending.materialized == ("quit", "Slack")
    assert pending.expires_at == 42.0

    detail = format_confirm_detail(
        step,
        (PlanStep(verb="site.search", slots={"name": "x"}),),
        materialized=("quit", "Slack"),
    )
    assert "quit Slack" in detail or "Slack" in detail
    assert "Then:" in detail
    assert "site.search" in detail
