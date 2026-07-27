"""Offline corpus: recorded LLM payloads → validated IntentPlan (no network)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from vaani.intent.llm import validate_plan_payload
from vaani.intent.schema import Result, RiskClass, SlotSpec, Status, Support, Verb
from vaani.platform.protocol import PlatformId

_FIXTURE = Path(__file__).resolve().parents[2] / "data" / "parse_utterances.json"
_SUPPORT = {
    PlatformId.LINUX: Support.SUPPORTED,
    PlatformId.MACOS: Support.SUPPORTED,
    PlatformId.WINDOWS: Support.SUPPORTED,
}


def _ok(_intent, _context):
    return Result(status=Status.OK, summary="ok")


def _verb(name: str, *, slots: dict[str, SlotSpec] | None = None) -> Verb:
    return Verb(
        name=name,
        title=name,
        slots=slots or {},
        rung=1,
        risk=RiskClass.R0,
        requires=frozenset(),
        support=_SUPPORT,
        undo=None,
        pack="test",
        handler=_ok,
    )


@pytest.fixture(scope="module")
def enabled_verbs() -> dict[str, Verb]:
    return {
        "app.open": _verb(
            "app.open", slots={"name": SlotSpec(type="str", required=True)}
        ),
        "site.search": _verb(
            "site.search",
            slots={
                "query": SlotSpec(type="str", required=True),
                "browser": SlotSpec(type="str", required=False),
            },
        ),
    }


def _rows() -> list[dict]:
    data = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    assert isinstance(data, list) and data
    return data


@pytest.mark.parametrize("row", _rows(), ids=lambda r: r["id"])
def test_parse_utterance_fixture(row: dict, enabled_verbs: dict[str, Verb]) -> None:
    plan = validate_plan_payload(
        row["payload"],
        enabled_verbs,
        utterance=row["utterance"],
        raw_utterance=row["utterance"],
    )
    expected = row["expected"]
    action = expected["action"]
    if action is None:
        assert plan is None
        return
    assert plan is not None
    if action == "refuse":
        assert plan.refuse_reason
        assert plan.steps == ()
        assert plan.delegate_prompt is None
    elif action == "delegate":
        assert plan.delegate_prompt
        assert plan.steps == ()
    else:
        assert plan.refuse_reason is None
        assert plan.delegate_prompt is None
        assert [s.verb for s in plan.steps] == list(expected["verbs"])
