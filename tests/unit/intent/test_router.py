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
    assert intent is not None
    assert intent.verb == "agent.task"
    assert intent.rung == 6


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


@pytest.mark.parametrize("row", _load_utterances(
    Path(__file__).resolve().parents[2] / "data" / "utterances.yaml"
))
def test_utterance_corpus(row: dict[str, object]) -> None:
    router = _router()
    intent = router.route(str(row["utterance"]), platform=PlatformId.LINUX)
    assert intent is not None
    assert intent.verb == row["verb"]
    if "rung" in row:
        assert intent.rung == row["rung"]
    not_verb = row.get("not_verb")
    if not_verb:
        assert intent.verb != not_verb
