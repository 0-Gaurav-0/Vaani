"""Unit tests for ConfirmEngine (T2.1)."""
from __future__ import annotations

from vaani.intent.schema import (
    Context,
    Intent,
    Result,
    RiskClass,
    SlotSpec,
    Status,
    Support,
    Verb,
)
from vaani.platform.protocol import PlatformId
from vaani.policy.confirm import ConfirmEngine, requires_confirm


def _intent(verb: str = "app.quit", **slots: object) -> Intent:
    return Intent(
        verb=verb,
        slots=slots,
        rung=1,
        confidence=1.0,
        source="test",
        mode="act",
        utterance="quit Slack",
        raw_utterance="quit Slack",
        modifiers=frozenset(),
        brain=None,
    )


def _context() -> Context:
    return Context(
        platform=PlatformId.LINUX,
        workspace=None,
        workspace_source="home",
        repo=None,
        project=None,
        focus=None,
        screen=None,
        session=None,
    )


def _verb(risk: RiskClass = RiskClass.R2, name: str = "app.quit") -> Verb:
    calls: list[str] = []

    def handler(intent: Intent, context: Context) -> Result:
        calls.append(intent.verb)
        return Result(status=Status.OK, summary="done", rung=1)

    verb = Verb(
        name=name,
        title="Quit",
        slots={"name": SlotSpec(type="str", required=True)},
        rung=1,
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
    object.__setattr__(verb, "_calls", calls)  # type: ignore[attr-defined]
    return verb


def test_requires_confirm_for_r2_plus() -> None:
    assert not requires_confirm(RiskClass.R0)
    assert not requires_confirm(RiskClass.R1)
    assert requires_confirm(RiskClass.R2)
    assert requires_confirm(RiskClass.R3)
    assert requires_confirm(RiskClass.R4)


def test_ttl_expiry() -> None:
    clock = {"now": 100.0}
    engine = ConfirmEngine(ttl=20.0, clock=lambda: clock["now"], id_factory=lambda: "a1")
    pending = engine.stage(
        _intent(name="Slack"),
        _verb(),
        ("wmctrl", "-c", "Slack"),
        context=_context(),
    )
    assert engine.peek() is pending
    clock["now"] = 119.0
    assert engine.expire_tick() is None
    assert engine.peek() is pending
    clock["now"] = 120.0
    expired = engine.expire_tick()
    assert expired is not None and expired.id == "a1"
    assert engine.peek() is None
    assert engine.approve("a1", via="pill") is None


def test_double_approve_rejected() -> None:
    engine = ConfirmEngine(id_factory=lambda: "once")
    engine.stage(
        _intent(name="Slack"),
        _verb(),
        ("wmctrl", "-c", "Slack"),
        context=_context(),
    )
    first = engine.approve("once", via="pill")
    assert first is not None
    assert engine.approve("once", via="pill") is None
    assert engine.claim_execution() is not None
    assert engine.claim_execution() is None


def test_utterance_invalidates_without_approving() -> None:
    verb = _verb()
    engine = ConfirmEngine(id_factory=lambda: "p1")
    engine.stage(
        _intent(name="Slack"),
        verb,
        ("wmctrl", "-c", "Slack"),
        context=_context(),
    )
    rejected = engine.invalidate()
    assert rejected is not None and rejected.id == "p1"
    assert engine.peek() is None
    assert engine.approve("p1", via="voice") is None
    assert getattr(verb, "_calls") == []


def test_enter_approve_and_esc_reject_via_strings() -> None:
    """Control-file / hotkey path: approve and reject by id."""
    engine = ConfirmEngine(id_factory=lambda: "k1")
    engine.stage(
        _intent(name="Slack"),
        _verb(),
        ("wmctrl", "-c", "Slack"),
        context=_context(),
    )
    assert engine.approve("k1", via="hotkey") is not None
    assert engine.claim_execution() is not None

    engine = ConfirmEngine(id_factory=lambda: "k2")
    engine.stage(
        _intent(name="Slack"),
        _verb(),
        ("wmctrl", "-c", "Slack"),
        context=_context(),
    )
    assert engine.reject("k2") is not None
    assert engine.peek() is None


def test_agent_via_blocked_on_r3_r4() -> None:
    for risk in (RiskClass.R3, RiskClass.R4):
        engine = ConfirmEngine(id_factory=lambda: "agent1")
        engine.stage(
            _intent(name="Slack"),
            _verb(risk=risk),
            ("rm", "-rf", "node_modules"),
            context=_context(),
        )
        assert engine.approve("agent1", via="agent") is None
        assert engine.peek() is not None
        assert engine.approve("agent1", via="pill") is not None


def test_agent_via_allowed_on_r2() -> None:
    engine = ConfirmEngine(id_factory=lambda: "agent2")
    engine.stage(
        _intent(name="Slack"),
        _verb(risk=RiskClass.R2),
        ("wmctrl", "-c", "Slack"),
        context=_context(),
    )
    assert engine.approve("agent2", via="agent") is not None
