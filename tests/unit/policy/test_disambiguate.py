"""Unit tests for DisambiguationEngine (T4.5)."""
from __future__ import annotations

from pathlib import Path

from vaani.intent.schema import (
    Context,
    DisambiguationOption,
    Intent,
    RiskClass,
    SlotSpec,
    Status,
    Support,
    Verb,
)
from vaani.platform.protocol import PlatformId, ProcInfo
from vaani.policy.disambiguate import (
    MAX_OPTIONS,
    DisambiguationEngine,
    build_prompt,
    disambiguation_result,
    options_from_procs,
    options_from_prs,
    options_from_workspaces,
    parse_ordinal_choice,
    truncate_options,
)


def _intent(verb: str = "system.proc.kill", **slots: object) -> Intent:
    return Intent(
        verb=verb,
        slots=slots,
        rung=2,
        confidence=1.0,
        source="test",
        mode="act",
        utterance="kill the process named node",
        raw_utterance="kill the process named node",
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


def _verb() -> Verb:
    def handler(intent: Intent, context: Context):
        from vaani.intent.schema import Result

        return Result(status=Status.OK, summary="done", rung=2)

    return Verb(
        name="system.proc.kill",
        title="Kill process",
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
        handler=handler,
    )


def _options(n: int) -> tuple[DisambiguationOption, ...]:
    return tuple(
        DisambiguationOption(
            key=str(i),
            label=f"opt-{i}",
            payload={"pid": i},
        )
        for i in range(1, n + 1)
    )


def test_truncate_options_to_three() -> None:
    trimmed = truncate_options(_options(5))
    assert len(trimmed) == MAX_OPTIONS == 3
    assert [o.key for o in trimmed] == ["1", "2", "3"]


def test_options_from_procs_truncates() -> None:
    procs = tuple(ProcInfo(pid=10 + i, name="node") for i in range(4))
    opts = options_from_procs(procs)
    assert len(opts) == 3
    assert opts[0].payload["pid"] == 10


def test_options_from_workspaces_and_prs_helpers() -> None:
    ws = options_from_workspaces([Path("/tmp/a"), Path("/tmp/b"), Path("/tmp/c"), Path("/tmp/d")])
    assert len(ws) == 3
    prs = options_from_prs(
        [
            {"number": 1, "title": "One"},
            {"number": 2, "title": "Two"},
            {"number": 3, "title": "Three"},
            {"number": 4, "title": "Four"},
        ]
    )
    assert len(prs) == 3
    assert prs[0].label.startswith("#1")


def test_parse_ordinal_first_second_third() -> None:
    assert parse_ordinal_choice("the first one") == 0
    assert parse_ordinal_choice("second") == 1
    assert parse_ordinal_choice("the third one") == 2
    assert parse_ordinal_choice("option 2") == 1
    assert parse_ordinal_choice("3") == 2
    assert parse_ordinal_choice("kill node") is None


def test_timeout_cancels_never_auto_picks() -> None:
    clock = {"now": 100.0}
    engine = DisambiguationEngine(
        ttl=20.0, clock=lambda: clock["now"], id_factory=lambda: "d1"
    )
    prompt = build_prompt(
        question="Which process?",
        options=_options(3),
        verb="system.proc.kill",
        slots={"name": "node"},
        prompt_id="d1",
        clock=lambda: clock["now"],
        ttl=20.0,
    )
    engine.stage(_intent(name="node"), _verb(), prompt, context=_context())
    assert engine.peek() is not None
    clock["now"] = 120.0
    expired = engine.expire_tick()
    assert expired is not None and expired.id == "d1"
    assert engine.peek() is None
    # Timeout must not leave a selectable default.
    assert engine.select("d1", 0, via="hotkey") is None
    assert engine.claim_selection() is None


def test_voice_ordinal_selection() -> None:
    engine = DisambiguationEngine(id_factory=lambda: "d2")
    prompt = build_prompt(
        question="Which process?",
        options=_options(3),
        verb="system.proc.kill",
        slots={"name": "node"},
        prompt_id="d2",
    )
    engine.stage(_intent(name="node"), _verb(), prompt, context=_context())
    chosen = engine.select_utterance("the second one", via="voice")
    assert chosen is not None and chosen.key == "2"
    bundle = engine.claim_selection()
    assert bundle is not None
    _intent_out, _verb_out, _ctx, _prompt, option = bundle
    assert option.payload["pid"] == 2
    assert engine.peek() is None


def test_hotkey_index_selection() -> None:
    engine = DisambiguationEngine(id_factory=lambda: "d3")
    prompt = build_prompt(
        question="Which?",
        options=_options(2),
        verb="system.proc.kill",
        prompt_id="d3",
    )
    engine.stage(_intent(name="node"), _verb(), prompt, context=_context())
    assert engine.select("d3", 0, via="hotkey") is not None
    assert engine.claim_selection() is not None


def test_disambiguation_result_status() -> None:
    result = disambiguation_result(
        question="Which node?",
        options=_options(4),
        verb="system.proc.kill",
        slots={"name": "node"},
        rung=2,
    )
    assert result.status is Status.NEEDS_DISAMBIGUATE
    assert result.disambiguation is not None
    assert len(result.disambiguation.options) == 3


def test_invalidate_never_selects() -> None:
    engine = DisambiguationEngine(id_factory=lambda: "d4")
    prompt = build_prompt(
        question="Which?",
        options=_options(2),
        verb="system.proc.kill",
        prompt_id="d4",
    )
    engine.stage(_intent(name="node"), _verb(), prompt, context=_context())
    rejected = engine.invalidate()
    assert rejected is not None
    assert engine.select("d4", 0, via="voice") is None
    assert engine.claim_selection() is None
