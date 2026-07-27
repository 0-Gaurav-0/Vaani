"""Unit tests for LLM plan payload validation and make_llm_parse (no network)."""
from __future__ import annotations

import json

from vaani.intent.llm import PARSE_SYSTEM_PROMPT, make_llm_parse, validate_plan_payload
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
from vaani.verbs.registry import Registry


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


class _StubGroq:
    def __init__(self, response: str | None) -> None:
        self.response = response
        self.calls: list[tuple[str, str, str]] = []

    def parse_intent(self, system: str, user: str, key: str, **_kwargs: object) -> str | None:
        self.calls.append((system, user, key))
        return self.response


def _registry_for_parse() -> Registry:
    registry = Registry()
    for verb in _enabled().values():
        registry.register(verb)
    return registry


def test_make_llm_parse_validates_fenced_plan() -> None:
    payload = {
        "action": "plan",
        "confidence": 0.88,
        "steps": [{"verb": "site.search", "slots": {"query": "Zapto.com"}}],
    }
    groq = _StubGroq(f"```json\n{json.dumps(payload)}\n```")
    parse = make_llm_parse(
        groq,
        lambda: "test-key",
        _registry_for_parse(),
        lambda: PlatformId.LINUX,
    )
    plan = parse("Search Zapto.com for me")
    assert isinstance(plan, IntentPlan)
    assert plan.source == "llm"
    assert plan.confidence == 0.88
    assert len(plan.steps) == 1
    assert plan.steps[0].verb == "site.search"
    assert plan.steps[0].slots["query"] == "Zapto.com"
    assert len(groq.calls) == 1
    system, user, key = groq.calls[0]
    assert system == PARSE_SYSTEM_PROMPT
    assert key == "test-key"
    user_obj = json.loads(user)
    assert user_obj["utterance"] == "Search Zapto.com for me"
    assert user_obj["catalog"]["platform"] == "linux"
    assert any(v["name"] == "site.search" for v in user_obj["catalog"]["verbs"])


def test_make_llm_parse_returns_none_on_bad_json() -> None:
    groq = _StubGroq("not json at all")
    parse = make_llm_parse(
        groq,
        lambda: "k",
        _registry_for_parse(),
        lambda: PlatformId.MACOS,
    )
    assert parse("hello") is None


def test_make_llm_parse_returns_none_when_groq_raises() -> None:
    class BoomGroq:
        def parse_intent(self, *_a: object, **_k: object) -> str:
            raise RuntimeError("network down")

    parse = make_llm_parse(
        BoomGroq(),
        lambda: "k",
        _registry_for_parse(),
        lambda: PlatformId.LINUX,
    )
    assert parse("search something") is None


def test_make_llm_parse_includes_context_blurb() -> None:
    payload = {
        "action": "refuse",
        "confidence": 1.0,
        "steps": [],
        "refuse_reason": "noop",
    }
    groq = _StubGroq(json.dumps(payload))
    parse = make_llm_parse(
        groq,
        lambda: "k",
        _registry_for_parse(),
        lambda: PlatformId.LINUX,
        get_context_blurb=lambda: "focus=Terminal",
    )
    plan = parse("thank you")
    assert plan is not None
    assert plan.refuse_reason == "noop"
    user_obj = json.loads(groq.calls[0][1])
    assert user_obj["context"] == "focus=Terminal"
