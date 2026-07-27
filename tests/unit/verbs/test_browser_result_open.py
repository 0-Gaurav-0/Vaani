"""Unit tests for structured browser search-result opening."""
from __future__ import annotations

from pathlib import Path

from vaani.intent.grammar import match
from vaani.intent.schema import Context, Intent, RiskClass, Status
from vaani.platform.protocol import PlatformId
from vaani.verbs.packs.browser_results import (
    BROWSER_RESULT_VERB_NAMES,
    browser_result_patterns,
    build_browser_result_verbs,
)


def _intent(
    slots: dict[str, object] | None = None,
    *,
    modifiers: frozenset[str] = frozenset({"confirmed"}),
) -> Intent:
    return Intent(
        verb="browser.result.open",
        slots=slots or {},
        rung=2,
        confidence=1.0,
        source="test",
        mode="act",
        utterance="open the first result",
        raw_utterance="open the first result",
        modifiers=modifiers,
        brain=None,
    )


def _context() -> Context:
    return Context(
        platform=PlatformId.MACOS,
        workspace=Path("/tmp/project"),
        workspace_source="test",
        repo=None,
        project=None,
        focus=None,
        screen=None,
        session=None,
    )


def test_open_first_result_uses_resolver_url() -> None:
    opened: list[str] = []
    verbs = {
        verb.name: verb
        for verb in build_browser_result_verbs(
            resolve_result_url=lambda index: (
                "https://en.wikipedia.org/wiki/Ramayana" if index == 1 else None
            ),
            open_url=lambda url, _intent: opened.append(url) or f"Opened {url}",
        )
    }

    result = verbs["browser.result.open"].handler(_intent(), _context())

    assert result.status is Status.OK
    assert opened == ["https://en.wikipedia.org/wiki/Ramayana"]
    assert result.evidence == ("https://en.wikipedia.org/wiki/Ramayana",)


def test_open_result_returns_degraded_result_when_resolver_misses() -> None:
    verb = build_browser_result_verbs(
        resolve_result_url=lambda _index: None,
        open_url=lambda url, _intent: f"Opened {url}",
    )[0]

    result = verb.handler(_intent(), _context())

    assert result.status is Status.PARTIAL
    assert result.summary == "Couldn't read results"
    assert "try guide or enable vision click" in result.detail


def test_open_result_is_r2_with_default_first_index() -> None:
    verb = build_browser_result_verbs(
        resolve_result_url=lambda _index: None,
        open_url=lambda url, _intent: f"Opened {url}",
    )[0]

    assert BROWSER_RESULT_VERB_NAMES == frozenset({"browser.result.open"})
    assert verb.risk is RiskClass.R2
    assert verb.slots["index"].type == "int"
    assert verb.slots["index"].default == 1


def test_open_first_result_grammar_includes_asr_side_variant() -> None:
    patterns = browser_result_patterns()

    assert match("open the first result", patterns) == (
        "browser.result.open",
        {"index": 1},
        60,
    )
    assert match("open first side", patterns) == (
        "browser.result.open",
        {"index": 1},
        60,
    )
