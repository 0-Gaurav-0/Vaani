"""Ladder router + utterance corpus (T0.4)."""
from __future__ import annotations

from pathlib import Path

import pytest

from vaani.apps import launch_app, resolve_app
from vaani.intent.router import Router
from vaani.platform.protocol import PlatformId
from vaani.sites import resolve_site
from vaani.verbs.packs.core import build_core_registry


def _load_utterances(path: Path) -> list[dict[str, object]]:
    """Minimal YAML subset loader for the utterances fixture (no PyYAML dep)."""
    rows: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if line.lstrip().startswith("- "):
            if current is not None:
                rows.append(current)
            current = {}
            rest = line.lstrip()[2:].strip()
            if rest and ":" in rest:
                key, value = rest.split(":", 1)
                current[key.strip()] = _parse_scalar(value.strip())
            continue
        if current is None:
            continue
        if ":" not in line:
            continue
        key, value = line.strip().split(":", 1)
        current[key.strip()] = _parse_scalar(value.strip())
    if current is not None:
        rows.append(current)
    return rows


def _parse_scalar(value: str) -> object:
    if value == "" or value == "null" or value == "~":
        return None
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    try:
        return int(value)
    except ValueError:
        return value


def _router(*, llm_parse=None) -> Router:
    from vaani.policy.undo import UndoStack, register_undo
    from vaani.verbs.packs.registry import register_stub_packs

    registry, patterns = build_core_registry(
        resolve_app_fn=resolve_app,
        launch_app_fn=launch_app,
        resolve_site_fn=resolve_site,
        open_browser_fn=lambda **_k: "Opened browser.",
    )
    patterns = patterns + register_undo(registry, UndoStack())
    # Installable packs — leave enabled (no PackRegistry.apply) so corpus
    # rows for those packs can resolve in unit tests.
    patterns = patterns + register_stub_packs(registry)
    return Router(
        registry,
        patterns,
        resolve_app=resolve_app,
        resolve_site=resolve_site,
        llm_parse=llm_parse,
    )


def test_open_claude_app_vs_website() -> None:
    router = _router()
    app = router.route("open Claude", platform=PlatformId.LINUX)
    site = router.route("open Claude website", platform=PlatformId.LINUX)
    assert app is not None and app.verb == "app.open" and app.rung == 1
    assert site is not None and site.verb == "site.open" and site.rung == 1


def test_browser_negative_does_not_match_allowlist() -> None:
    router = _router()
    intent = router.route("open chrome and run ls", platform=PlatformId.LINUX)
    # Grammar miss + no llm_parse → None (no blind agent.task fallback).
    assert intent is None


def test_rung_1_2_resolve_with_brain_mocked_to_raise() -> None:
    def boom(_text: str):
        raise AssertionError("LLM parser must not be called for rung 1/2")

    router = _router(llm_parse=boom)
    corpus = _load_utterances(
        Path(__file__).resolve().parents[2] / "data" / "utterances.yaml"
    )
    for row in corpus:
        verb = row.get("verb")
        rung = row.get("rung")
        if verb in {None, "agent.task"}:
            continue
        if not isinstance(rung, int) or rung > 2:
            continue
        intent = router.route(str(row["utterance"]), platform=PlatformId.LINUX)
        assert intent is not None, row
        assert intent.verb == verb, row
        assert intent.rung == rung, row
        # Brain must remain unused.
        assert router.llm_parse is boom


def test_grammar_hit_never_calls_llm_parse() -> None:
    calls: list[str] = []

    def spy(text: str):
        calls.append(text)
        raise AssertionError("llm_parse must not run on grammar hit")

    router = _router(llm_parse=spy)
    intent = router.route("open Terminal", platform=PlatformId.LINUX)
    assert intent is not None
    assert intent.verb == "app.open"
    assert intent.source == "grammar"
    assert calls == []


def test_miss_llm_one_step_returns_intent() -> None:
    from vaani.intent.schema import Intent, IntentPlan, PlanStep

    def parse(_text: str) -> IntentPlan:
        return IntentPlan(
            steps=(PlanStep(verb="site.search", slots={"query": "Zapto.com"}),),
            utterance="search zapto.com for me",
            raw_utterance="Search Zapto.com for me",
            source="llm",
            confidence=0.9,
        )

    router = _router(llm_parse=parse)
    intent = router.route("Search Zapto.com for me", platform=PlatformId.LINUX)
    assert isinstance(intent, Intent)
    assert intent.verb == "site.search"
    assert intent.source == "llm"
    assert intent.confidence == 0.9
    assert intent.slots["query"] == "Zapto.com"


def test_miss_llm_refuse_returns_none() -> None:
    from vaani.intent.schema import IntentPlan

    def parse(_text: str) -> IntentPlan:
        return IntentPlan(
            steps=(),
            utterance="thank you",
            raw_utterance="Thank you.",
            source="llm",
            confidence=1.0,
            refuse_reason="polite noop",
        )

    router = _router(llm_parse=parse)
    assert router.route("Thank you.", platform=PlatformId.LINUX) is None


def test_miss_llm_delegate_returns_agent_task() -> None:
    from vaani.intent.schema import Intent, IntentPlan

    def parse(_text: str) -> IntentPlan:
        return IntentPlan(
            steps=(),
            utterance="fix the failing test",
            raw_utterance="fix the failing test",
            source="llm",
            confidence=0.8,
            delegate_prompt="fix the failing test",
        )

    router = _router(llm_parse=parse)
    intent = router.route("fix the failing test", platform=PlatformId.LINUX)
    assert isinstance(intent, Intent)
    assert intent.verb == "agent.task"
    assert intent.source == "llm"
    assert intent.rung == 6
    assert intent.slots["prompt"] == "fix the failing test"


def test_miss_llm_raise_returns_none() -> None:
    def boom(_text: str):
        raise RuntimeError("parse failed")

    router = _router(llm_parse=boom)
    assert router.route("gibberish xyz", platform=PlatformId.LINUX) is None


def test_miss_llm_multi_step_returns_plan() -> None:
    from vaani.intent.schema import IntentPlan, PlanStep

    def parse(_text: str) -> IntentPlan:
        return IntentPlan(
            steps=(
                PlanStep(verb="app.open", slots={"name": "Brave Browser"}),
                PlanStep(verb="site.search", slots={"query": "Zepter"}),
            ),
            utterance="open brave and search zepter",
            raw_utterance="open Brave and search Zepter",
            source="llm",
            confidence=0.85,
        )

    router = _router(llm_parse=parse)
    plan = router.route("open Brave and search Zepter", platform=PlatformId.LINUX)
    assert isinstance(plan, IntentPlan)
    assert len(plan.steps) == 2
    assert plan.source == "llm"


def test_compound_beats_resolve_app_with_llm_plan() -> None:
    """Live bug: 'Open Chrome and search X' was stolen by app.open before LLM."""
    from types import SimpleNamespace

    from vaani.intent.schema import IntentPlan, PlanStep

    calls: list[str] = []

    def parse(text: str) -> IntentPlan:
        calls.append(text)
        return IntentPlan(
            steps=(
                PlanStep(verb="app.open", slots={"name": "Google Chrome"}),
                PlanStep(verb="site.search", slots={"query": "Zapto.com"}),
            ),
            utterance=text,
            raw_utterance=text,
            source="llm",
            confidence=0.9,
        )

    chrome = SimpleNamespace(name="Google Chrome")
    registry, patterns = build_core_registry(
        resolve_app_fn=lambda _t: chrome,
        launch_app_fn=lambda *_a, **_k: "ok",
        resolve_site_fn=lambda _t: None,
        open_browser_fn=lambda **_k: "ok",
    )
    router = Router(
        registry,
        patterns,
        resolve_app=lambda _t: chrome,
        resolve_site=lambda _t: None,
        llm_parse=parse,
    )
    plan = router.route(
        "Open Chrome and search Zapto.com", platform=PlatformId.LINUX
    )
    assert isinstance(plan, IntentPlan)
    assert [s.verb for s in plan.steps] == ["app.open", "site.search"]
    assert calls, "llm_parse must run for compound open+search"


def test_simple_open_still_skips_llm() -> None:
    from types import SimpleNamespace

    def boom(_text: str):
        raise AssertionError("llm_parse must not run for plain app.open")

    chrome = SimpleNamespace(name="Google Chrome")
    registry, patterns = build_core_registry(
        resolve_app_fn=lambda _t: chrome,
        launch_app_fn=lambda *_a, **_k: "ok",
        resolve_site_fn=lambda _t: None,
        open_browser_fn=lambda **_k: "ok",
    )
    router = Router(
        registry,
        patterns,
        resolve_app=lambda _t: chrome,
        resolve_site=lambda _t: None,
        llm_parse=boom,
    )
    intent = router.route("Open Chrome", platform=PlatformId.LINUX)
    assert intent is not None
    assert intent.verb == "app.open"
    assert intent.source == "grammar"


@pytest.mark.parametrize("row", _load_utterances(
    Path(__file__).resolve().parents[2] / "data" / "utterances.yaml"
))
def test_utterance_corpus(row: dict[str, object]) -> None:
    router = _router()
    intent = router.route(str(row["utterance"]), platform=PlatformId.LINUX)
    expected = row["verb"]
    if expected is None:
        assert intent is None
        return
    assert intent is not None
    assert intent.verb == expected
    if "rung" in row:
        assert intent.rung == row["rung"]
    not_verb = row.get("not_verb")
    if not_verb:
        assert intent.verb != not_verb
