"""Unit tests for UndoStack + session.undo (T2.3)."""
from __future__ import annotations

from typing import Any

import pytest

from vaani.intent.grammar import match
from vaani.intent.schema import (
    Context,
    Intent,
    Result,
    RiskClass,
    SlotSpec,
    Status,
    Support,
    UndoToken,
    Verb,
)
from vaani.platform.protocol import PlatformId
from vaani.policy.dryrun import dispatch
from vaani.policy.undo import (
    MAX_STACK,
    UndoClass,
    UndoStack,
    build_inverse_token,
    classify_verb,
    irreversible_refusal,
    inverse_slots,
    register_undo,
    undo_patterns,
)
from vaani.verbs.registry import Registry


def _context() -> Context:
    return Context(
        platform=PlatformId.LINUX,
        workspace=None,
        workspace_source="test",
        repo=None,
        project=None,
        focus=None,
        screen=None,
        session=None,
    )


def _intent(verb: str, slots: dict[str, Any] | None = None, **mods: bool) -> Intent:
    modifiers = frozenset(name for name, on in mods.items() if on)
    return Intent(
        verb=verb,
        slots=slots or {},
        rung=1,
        confidence=1.0,
        source="test",
        mode="act",
        utterance=verb,
        raw_utterance=verb,
        modifiers=modifiers,
        brain=None,
    )


def _toggle_verb(name: str, slot_key: str = "enabled") -> Verb:
    calls: list[dict[str, Any]] = []

    def handler(intent: Intent, _context: Context) -> Result:
        calls.append(dict(intent.slots))
        token = build_inverse_token(name, intent.slots)
        return Result(
            status=Status.OK,
            summary=f"{name} ok",
            rung=1,
            undo=token,
        )

    verb = Verb(
        name=name,
        title=name,
        slots={slot_key: SlotSpec(type="bool", required=True)},
        rung=1,
        risk=RiskClass.R0,
        requires=frozenset(),
        support={
            PlatformId.LINUX: Support.SUPPORTED,
            PlatformId.MACOS: Support.SUPPORTED,
            PlatformId.WINDOWS: Support.SUPPORTED,
        },
        undo=name,
        pack="test",
        handler=handler,
    )
    object.__setattr__(verb, "calls", calls)
    return verb


def test_classify_three_classes() -> None:
    assert classify_verb("system.volume.set") is UndoClass.REVERSIBLE
    assert classify_verb("system.dnd.set") is UndoClass.REVERSIBLE
    assert classify_verb("system.wifi.set") is UndoClass.REVERSIBLE
    assert classify_verb("vcs.stash.push") is UndoClass.COMPENSATABLE
    assert classify_verb("vcs.branch.create") is UndoClass.COMPENSATABLE
    assert classify_verb("files.mkdir") is UndoClass.COMPENSATABLE
    assert classify_verb("files.sweep") is UndoClass.COMPENSATABLE
    assert classify_verb("system.proc.kill") is UndoClass.IRREVERSIBLE
    assert classify_verb("system.trash.empty") is UndoClass.IRREVERSIBLE
    assert classify_verb("app.open") is None


def test_inverse_pairs_volume_dnd_wifi() -> None:
    assert inverse_slots("system.volume.set", {"muted": True}) == {"muted": False}
    assert inverse_slots("system.volume.set", {"muted": False}) == {"muted": True}
    assert inverse_slots("system.volume.set", {"level": 40, "previous_level": 20}) == {
        "level": 20
    }
    assert inverse_slots("system.volume.set", {"level": 40}) is None
    assert inverse_slots("system.dnd.set", {"enabled": True}) == {"enabled": False}
    assert inverse_slots("system.wifi.set", {"enabled": False}) == {"enabled": True}


def test_inverse_pairs_compensatable_synthetic() -> None:
    assert inverse_slots("vcs.stash.push", {"message": "wip"}) == {"message": "wip"}
    assert inverse_slots(
        "files.sweep",
        {"source": "/desktop", "dest": "/screenshots"},
    ) == {"source": "/screenshots", "dest": "/desktop"}
    token = build_inverse_token("vcs.branch.create", {"name": "feat/x"})
    assert token is not None
    assert token.inverse_verb == "vcs.branch.delete"
    assert token.slots["name"] == "feat/x"


def test_stack_bound_10() -> None:
    clock = {"t": 100.0}
    stack = UndoStack(clock=lambda: clock["t"], ttl=60.0)
    for i in range(MAX_STACK + 5):
        stack.push_inverse(
            UndoToken(
                verb="system.dnd.set",
                inverse_verb="system.dnd.set",
                slots={"enabled": False},
                expires_at=clock["t"] + 60.0,
            ),
            undo_class=UndoClass.REVERSIBLE,
        )
    assert len(stack) == MAX_STACK
    top = stack.peek()
    assert top is not None
    # Newest kept; oldest dropped.
    assert top.token.verb == "system.dnd.set"


def test_stack_expiry() -> None:
    clock = {"t": 0.0}
    stack = UndoStack(clock=lambda: clock["t"], ttl=10.0)
    stack.push_inverse(
        UndoToken(
            verb="system.wifi.set",
            inverse_verb="system.wifi.set",
            slots={"enabled": True},
            expires_at=10.0,
        )
    )
    assert len(stack) == 1
    clock["t"] = 10.0
    assert stack.pop() is None
    assert len(stack) == 0


def test_irreversible_refusal_text() -> None:
    assert irreversible_refusal("system.proc.kill") == (
        "killing a process can't be undone"
    )
    assert irreversible_refusal("system.port.free") == (
        "killing a process can't be undone"
    )
    assert "trash" in irreversible_refusal("system.trash.empty")


def test_session_undo_refuses_irreversible() -> None:
    stack = UndoStack(clock=lambda: 0.0)
    registry = Registry()
    register_undo(registry, stack)
    kill = Verb(
        name="system.proc.kill",
        title="Kill",
        slots={"name": SlotSpec(type="str", required=True)},
        rung=2,
        risk=RiskClass.R2,
        requires=frozenset(),
        support={
            PlatformId.LINUX: Support.SUPPORTED,
            PlatformId.MACOS: Support.SUPPORTED,
            PlatformId.WINDOWS: Support.SUPPORTED,
        },
        undo=None,
        pack="procs",
        handler=lambda _i, _c: Result(status=Status.OK, summary="killed", rung=2),
    )
    registry.register(kill)
    stack.record_success(
        kill,
        _intent("system.proc.kill", {"name": "node"}),
        Result(status=Status.OK, summary="killed", rung=2),
    )
    undo = registry.get("session.undo")
    assert undo is not None
    result = undo.handler(_intent("session.undo"), _context())
    assert result.status is Status.REFUSED
    assert result.summary == "killing a process can't be undone"
    # No partial attempt — stack entry consumed, inverse never run.
    assert len(stack) == 0


def test_session_undo_inverse_toggle() -> None:
    stack = UndoStack(clock=lambda: 0.0)
    registry = Registry()
    dnd = _toggle_verb("system.dnd.set")
    registry.register(dnd)
    register_undo(registry, stack)

    stack.record_success(
        dnd,
        _intent("system.dnd.set", {"enabled": True}),
        Result(
            status=Status.OK,
            summary="on",
            rung=1,
            undo=build_inverse_token("system.dnd.set", {"enabled": True}),
        ),
    )
    undo = registry.get("session.undo")
    assert undo is not None
    result = undo.handler(_intent("session.undo"), _context())
    assert result.status is Status.OK
    assert dnd.calls == [{"enabled": False}]  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "verb_name,slots",
    [
        ("system.volume.set", {"muted": True}),
        ("system.dnd.set", {"enabled": True}),
        ("system.wifi.set", {"enabled": False}),
    ],
)
def test_record_success_inverse_pairs(verb_name: str, slots: dict[str, Any]) -> None:
    stack = UndoStack(clock=lambda: 1.0, ttl=60.0)
    verb = _toggle_verb(verb_name, slot_key=next(iter(slots)))
    token = build_inverse_token(verb_name, slots, clock=lambda: 1.0)
    assert token is not None
    stack.record_success(
        verb,
        _intent(verb_name, slots),
        Result(status=Status.OK, summary="ok", rung=1, undo=token),
    )
    entry = stack.peek()
    assert entry is not None
    assert entry.undo_class is UndoClass.REVERSIBLE
    assert entry.token.slots == inverse_slots(verb_name, slots)


def test_session_undo_dry_run_safe() -> None:
    stack = UndoStack(clock=lambda: 0.0)
    registry = Registry()
    dnd = _toggle_verb("system.dnd.set")
    registry.register(dnd)
    register_undo(registry, stack)
    stack.push_inverse(
        UndoToken(
            verb="system.dnd.set",
            inverse_verb="system.dnd.set",
            slots={"enabled": False},
            expires_at=60.0,
        )
    )
    undo = registry.get("session.undo")
    assert undo is not None
    result = dispatch(undo, _intent("session.undo", dry_run=True), _context())
    assert result.status is Status.DRY_RUN
    assert result.evidence == ("session.undo",)
    assert len(stack) == 1
    assert dnd.calls == []  # type: ignore[attr-defined]


def test_undo_patterns_match() -> None:
    patterns = undo_patterns()
    hit = match("undo that", patterns)
    assert hit is not None
    assert hit[0] == "session.undo"
    assert match("undo", patterns)[0] == "session.undo"


def test_empty_stack_fails_clearly() -> None:
    stack = UndoStack()
    registry = Registry()
    register_undo(registry, stack)
    undo = registry.get("session.undo")
    assert undo is not None
    result = undo.handler(_intent("session.undo"), _context())
    assert result.status is Status.FAILED
    assert "Nothing to undo" in result.summary
