"""T2.5 interrogative → refuse / say-as-command (parallel-agents override)."""
from __future__ import annotations

from pathlib import Path

import pytest

from vaani.apps import launch_app, resolve_app
from vaani.intent.interrogative import (
    is_interrogative,
    port_query_slots,
    refuse_interrogative,
    should_refuse_interrogative,
    strip_interrogative_lead,
    suggest_command,
)
from vaani.intent.router import Router
from vaani.intent.schema import (
    Intent,
    RiskClass,
    SlotSpec,
    Status,
    Support,
    Verb,
)
from vaani.platform.protocol import PlatformId
from vaani.sites import resolve_site
from vaani.verbs.packs.core import build_core_registry


def _load_utterances(path: Path) -> list[dict[str, object]]:
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
    if value in {"null", "~", ""}:
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


def _router() -> Router:
    registry, patterns = build_core_registry(
        resolve_app_fn=resolve_app,
        launch_app_fn=launch_app,
        resolve_site_fn=resolve_site,
        open_browser_fn=lambda **_k: "Opened browser.",
    )
    return Router(
        registry,
        patterns,
        resolve_app=resolve_app,
        resolve_site=resolve_site,
    )


@pytest.mark.parametrize(
    "spoken,expected",
    [
        ("how do I free port 3000", True),
        ("what's on port 3000", True),
        ("what is on port 8080", True),
        ("where is the export button", True),
        ("can you free port 3000", True),
        ("can I kill the process named node", True),
        ("who's using port 3000", True),
        ("is there something on port 3000", True),
        ("free port 3000", False),
        ("kill the process named node", False),
        ("empty the trash", False),
        # Mid-utterance "what's" must not count as interrogative lead.
        ("show what's using the most CPU", False),
        ("", False),
    ],
)
def test_is_interrogative_heuristics(spoken: str, expected: bool) -> None:
    assert is_interrogative(spoken) is expected


@pytest.mark.parametrize(
    "spoken,rest",
    [
        ("how do I free port 3000", "free port 3000"),
        ("can you free port 3000", "free port 3000"),
        ("what's on port 3000", "on port 3000"),
        ("who's using port 3000", "using port 3000"),
        ("free port 3000", "free port 3000"),
    ],
)
def test_strip_interrogative_lead(spoken: str, rest: str) -> None:
    assert strip_interrogative_lead(spoken) == rest


def test_port_query_slots() -> None:
    assert port_query_slots("what's on port 3000") == {
        "port": 3000,
        "signal": "term",
    }
    assert port_query_slots("who's using port 8080") == {
        "port": 8080,
        "signal": "term",
    }
    assert port_query_slots("free port 3000") is None


def test_suggest_command_phrases() -> None:
    assert suggest_command("system.port.free", {"port": 3000}) == "free port 3000"
    assert (
        suggest_command("system.proc.kill", {"name": "node"})
        == "kill the process named node"
    )
    assert suggest_command("system.trash.empty", {}) == "empty the trash"


def test_refuse_result_is_short_say_as_command() -> None:
    verb = Verb(
        name="system.port.free",
        title="Free port",
        slots={"port": SlotSpec(type="int")},
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
        handler=lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no handler")),
    )
    intent = Intent(
        verb="system.port.free",
        slots={"port": 3000},
        rung=2,
        confidence=1.0,
        source="grammar",
        mode="act",
        utterance="free port 3000",
        raw_utterance="how do I free port 3000",
        modifiers=frozenset({"interrogative"}),
        brain=None,
    )
    assert should_refuse_interrogative(verb, intent)
    result = refuse_interrogative(verb, intent)
    assert result.status is Status.REFUSED
    assert "say it as a command" in result.summary.casefold()
    assert "free port 3000" in result.summary.casefold()
    assert result.pending is None
    assert result.overlay == ()


_CORPUS = _load_utterances(
    Path(__file__).resolve().parents[2] / "data" / "utterances.yaml"
)
_INTERROGATIVE_ROWS = [row for row in _CORPUS if row.get("interrogative") is True]


@pytest.mark.parametrize("row", _INTERROGATIVE_ROWS)
def test_interrogative_corpus_routes_with_modifier(row: dict[str, object]) -> None:
    router = _router()
    intent = router.route(str(row["utterance"]), platform=PlatformId.LINUX)
    assert intent is not None, row
    assert intent.verb == row["verb"], row
    assert "interrogative" in intent.modifiers, row
    if "rung" in row:
        assert intent.rung == row["rung"], row


@pytest.mark.parametrize("row", _INTERROGATIVE_ROWS)
def test_interrogative_corpus_refuses_without_handler(row: dict[str, object]) -> None:
    router = _router()
    intent = router.route(str(row["utterance"]), platform=PlatformId.LINUX)
    assert intent is not None
    verb = router.registry.get(str(row["verb"]))
    assert verb is not None
    assert verb.risk is RiskClass[str(row["risk"])]
    assert should_refuse_interrogative(verb, intent)

    calls: list[str] = []
    original = verb.handler

    def spy(i, ctx):
        calls.append(i.verb)
        return original(i, ctx)

    object.__setattr__(verb, "handler", spy)
    result = refuse_interrogative(verb, intent)
    assert result.status is Status.REFUSED
    assert calls == []
    assert "say it as a command" in result.summary.casefold()
    # Suggested imperative appears when we can phrase one.
    if verb.name == "system.port.free":
        assert "free port" in result.summary.casefold()
    elif verb.name == "system.proc.kill":
        assert "kill" in result.summary.casefold()
    elif verb.name == "system.trash.empty":
        assert "trash" in result.summary.casefold()
