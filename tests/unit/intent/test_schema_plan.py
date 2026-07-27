"""Unit tests for IntentPlan / PlanStep contracts (LLM parse)."""
from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from vaani.intent.schema import IntentPlan, PlanStep


def test_intent_plan_two_steps_frozen() -> None:
    steps = (
        PlanStep(verb="app.open", slots={"name": "Brave Browser"}),
        PlanStep(verb="site.search", slots={"query": "Zepter"}, note="then search"),
    )
    plan = IntentPlan(
        steps=steps,
        utterance="open brave and search zepter",
        raw_utterance="open Brave and search Zepter",
        source="llm",
        confidence=0.9,
    )

    assert len(plan.steps) == 2
    assert plan.steps[0].verb == "app.open"
    assert plan.steps[1].slots["query"] == "Zepter"
    assert plan.steps[1].note == "then search"
    assert plan.delegate_prompt is None
    assert plan.refuse_reason is None
    assert isinstance(plan.steps, tuple)

    with pytest.raises(FrozenInstanceError):
        plan.confidence = 0.1  # type: ignore[misc]

    with pytest.raises(FrozenInstanceError):
        plan.steps[0].verb = "other"  # type: ignore[misc]


def test_intent_plan_delegate_and_refuse_fields() -> None:
    delegate = IntentPlan(
        steps=(),
        utterance="fix the failing test",
        raw_utterance="fix the failing test",
        source="llm",
        confidence=0.8,
        delegate_prompt="fix the failing test",
    )
    assert delegate.steps == ()
    assert delegate.delegate_prompt == "fix the failing test"

    refuse = IntentPlan(
        steps=(),
        utterance="thank you",
        raw_utterance="Thank you.",
        source="llm",
        confidence=1.0,
        refuse_reason="polite noop",
    )
    assert refuse.refuse_reason == "polite noop"
    assert refuse.source == "llm"
