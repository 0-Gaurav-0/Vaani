"""Unit tests for LLM plan payload validation (no network)."""
from __future__ import annotations

from vaani.intent.llm import validate_plan_payload
from vaani.intent.schema import (
    Context,
    Intent,
    IntentPlan,
    Result,
    RiskClass,
    SlotSpec,
    Status,
    Support,
    Verb,
)
from vaani.platform.protocol import PlatformId


def _ok(_intent: Intent, _context: Context) -> Result:
    return Result(status=Status.OK, summary="ok")


def _verb(
    name: str,
    *,
    slots: dict[str, SlotSpec] | None = None,
    risk: RiskClass = RiskClass.R0,
    rung: int = 1,
) -> Verb:
    return Verb(
        name=name,
        title=name,
        slots=slots or {"query": SlotSpec(type="str")},
        rung=rung,
        risk=risk,
        requires=frozenset(),
        support={
            PlatformId.LINUX: Support.SUPPORTED,
            PlatformId.MACOS: Support.SUPPORTED,
            PlatformId.WINDOWS: Support.SUPPORTED,
        },
        undo=None,
        pack="core",
        handler=_ok,
    )


def _enabled() -> dict[str, Verb]:
    return {
        "app.open": _verb(
            "app.open",
            slots={"name": SlotSpec(type="str")},
        ),
        "site.search": _verb(
            "site.search",
            slots={
                "query": SlotSpec(type="str"),
                "browser": SlotSpec(type="str", required=False),
            },
        ),
        "system.port.free": _verb(
            "system.port.free",
            slots={"port": SlotSpec(type="int")},
        ),
    }


def test_validate_valid_two_step_plan() -> None:
    payload = {
        "action": "plan",
        "confidence": 0.91,
        "steps": [
            {"verb": "app.open", "slots": {"name": "Brave Browser"}},
            {
                "verb": "site.search",
                "slots": {"query": "Zepter", "browser": "brave"},
                "note": "then search",
            },
        ],
    }
    plan = validate_plan_payload(
        payload,
        _enabled(),
        utterance="open brave and search zepter",
        raw_utterance="open Brave and search Zepter",
    )
    assert isinstance(plan, IntentPlan)
    assert plan.source == "llm"
    assert plan.confidence == 0.91
    assert len(plan.steps) == 2
    assert plan.steps[0].verb == "app.open"
    assert plan.steps[1].verb == "site.search"
    assert plan.steps[1].note == "then search"
    assert plan.delegate_prompt is None
    assert plan.refuse_reason is None


def test_validate_unknown_verb_rejects() -> None:
    payload = {
        "action": "plan",
        "confidence": 0.7,
        "steps": [{"verb": "shell.run", "slots": {"cmd": "ls"}}],
    }
    assert (
        validate_plan_payload(
            payload,
            _enabled(),
            utterance="run ls",
            raw_utterance="run ls",
        )
        is None
    )


def test_validate_refuse_action() -> None:
    payload = {
        "action": "refuse",
        "confidence": 1.0,
        "steps": [],
        "refuse_reason": "polite noop",
    }
    plan = validate_plan_payload(
        payload,
        _enabled(),
        utterance="thank you",
        raw_utterance="Thank you.",
    )
    assert plan is not None
    assert plan.source == "llm"
    assert plan.steps == ()
    assert plan.refuse_reason == "polite noop"
    assert plan.delegate_prompt is None


def test_validate_delegate_action() -> None:
    payload = {
        "action": "delegate",
        "confidence": 0.85,
        "steps": [],
        "delegate_prompt": "fix the failing test",
    }
    plan = validate_plan_payload(
        payload,
        _enabled(),
        utterance="fix the failing test",
        raw_utterance="fix the failing test",
    )
    assert plan is not None
    assert plan.source == "llm"
    assert plan.steps == ()
    assert plan.delegate_prompt == "fix the failing test"
    assert plan.refuse_reason is None


def test_validate_max_steps_rejects() -> None:
    steps = [
        {"verb": "site.search", "slots": {"query": f"q{i}"}} for i in range(7)
    ]
    payload = {"action": "plan", "confidence": 0.5, "steps": steps}
    assert (
        validate_plan_payload(
            payload,
            _enabled(),
            utterance="too many",
            raw_utterance="too many",
        )
        is None
    )


def test_validate_missing_required_slot_rejects() -> None:
    payload = {
        "action": "plan",
        "confidence": 0.8,
        "steps": [{"verb": "site.search", "slots": {}}],
    }
    assert (
        validate_plan_payload(
            payload,
            _enabled(),
            utterance="search something",
            raw_utterance="search something",
        )
        is None
    )


def test_validate_int_slot_coercion() -> None:
    payload = {
        "action": "plan",
        "confidence": 0.9,
        "steps": [{"verb": "system.port.free", "slots": {"port": "3000"}}],
    }
    plan = validate_plan_payload(
        payload,
        _enabled(),
        utterance="killport 3000",
        raw_utterance="Killport 3000",
    )
    assert plan is not None
    assert plan.steps[0].slots["port"] == 3000
