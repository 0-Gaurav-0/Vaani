"""Dry-run + materialization (T2.2)."""
from __future__ import annotations

from typing import Any

import pytest

from vaani.cli import build_registry, cmd_do
from vaani.intent.normalize import detect_modifiers, normalize
from vaani.intent.router import Router
from vaani.intent.schema import Context, Intent, Result, RiskClass, SlotSpec, Status, Support, Verb
from vaani.platform.protocol import PlatformId
from vaani.policy.dryrun import dispatch, materialize_argv
from vaani.verbs.packs.core import CORE_VERB_NAMES, build_core_registry
from vaani.verbs.registry import Registry


def _context(platform: PlatformId = PlatformId.MACOS) -> Context:
    return Context(
        platform=platform,
        workspace=None,
        workspace_source="test",
        repo=None,
        project=None,
        focus=None,
        screen=None,
        session=None,
    )


def _intent(verb: str, *, slots: dict[str, Any] | None = None, dry_run: bool = True) -> Intent:
    return Intent(
        verb=verb,
        slots=slots or {},
        rung=1,
        confidence=1.0,
        source="test",
        mode="act",
        utterance=verb,
        raw_utterance=verb,
        modifiers=frozenset({"dry_run"} if dry_run else ()),
        brain=None,
    )


def _spy_verb(name: str, calls: list[str]) -> Verb:
    def handler(_intent: Intent, _context: Context) -> Result:
        calls.append(name)
        return Result(status=Status.OK, summary="ran", rung=1)

    return Verb(
        name=name,
        title=name,
        slots={"name": SlotSpec(type="str", required=False)},
        rung=1,
        risk=RiskClass.R0,
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


@pytest.mark.parametrize("verb_name", sorted(CORE_VERB_NAMES))
def test_dry_run_never_invokes_handler(verb_name: str) -> None:
    calls: list[str] = []
    registry = Registry()
    for name in sorted(CORE_VERB_NAMES):
        registry.register(_spy_verb(name, calls))

    verb = registry.get(verb_name)
    assert verb is not None
    slots: dict[str, Any] = {"name": "Terminal"} if "name" in verb.slots else {}
    if verb_name == "system.volume.set":
        slots = {"level": "30"}
    elif verb_name == "system.dnd.set":
        slots = {"enabled": True}
    elif verb_name == "site.search":
        slots = {"query": "vaani"}
    elif verb_name == "files.open_dir":
        slots = {"folder": "downloads"}
    elif verb_name == "agent.task":
        slots = {"prompt": "list files"}

    result = dispatch(verb, _intent(verb_name, slots=slots, dry_run=True), _context())
    assert result.status is Status.DRY_RUN
    assert result.evidence  # argv materialized or fallback
    assert calls == []


def test_dispatch_without_dry_run_calls_handler() -> None:
    calls: list[str] = []
    verb = _spy_verb("app.open", calls)
    result = dispatch(verb, _intent("app.open", dry_run=False), _context())
    assert result.status is Status.OK
    assert calls == ["app.open"]


def test_cli_dry_run_uses_dispatch_not_handler(capsys: pytest.CaptureFixture[str]) -> None:
    calls: list[str] = []
    registry = Registry()
    registry.register(_spy_verb("app.open", calls))
    code = cmd_do(
        "app.open",
        [("name", "Terminal")],
        as_json=True,
        dry_run=True,
        platform=PlatformId.MACOS,
        registry=registry,
    )
    assert code == 0
    assert calls == []
    out = capsys.readouterr().out
    assert '"status":"dry_run"' in out or '"status": "dry_run"' in out.replace(" ", "")


@pytest.mark.parametrize(
    ("spoken", "expected_text", "has_dry_run"),
    [
        ("don't run it open chrome", "open chrome", True),
        ("dont run it open terminal", "open terminal", True),
        ("just show me open chrome", "open chrome", True),
        ("just show me the command open chrome", "open chrome", True),
        ("what would you run open chrome", "open chrome", True),
        ("don't run it — just show me open chrome", "open chrome", True),
        ("open chrome", "open chrome", False),
        ("please open chrome", "open chrome", False),
    ],
)
def test_normalize_detects_and_strips_dry_run(
    spoken: str,
    expected_text: str,
    has_dry_run: bool,
) -> None:
    assert detect_modifiers(spoken) == (frozenset({"dry_run"}) if has_dry_run else frozenset())
    assert normalize(spoken) == expected_text


def test_router_sets_dry_run_modifier() -> None:
    from vaani.apps import launch_app, resolve_app
    from vaani.sites import resolve_site

    registry, patterns = build_core_registry(
        resolve_app_fn=resolve_app,
        launch_app_fn=launch_app,
        resolve_site_fn=resolve_site,
        open_browser_fn=lambda **_k: "Opened browser.",
    )
    router = Router(
        registry,
        patterns,
        resolve_app=resolve_app,
        resolve_site=resolve_site,
    )
    intent = router.route("don't run it open Terminal", platform=PlatformId.MACOS)
    assert intent is not None
    assert intent.verb == "app.open"
    assert "dry_run" in intent.modifiers
    assert "don't" not in intent.utterance
    assert "run" not in intent.utterance or "terminal" in intent.utterance


def test_materialize_app_open_macos() -> None:
    argv = materialize_argv("app.open", {"name": "Terminal"}, platform=PlatformId.MACOS)
    assert argv == ("open", "-a", "Terminal")


def test_build_registry_dry_run_via_cli(capsys: pytest.CaptureFixture[str]) -> None:
    code = cmd_do(
        "system.lock",
        [],
        as_json=True,
        dry_run=True,
        platform=PlatformId.MACOS,
        registry=build_registry(PlatformId.MACOS),
    )
    assert code == 0
    import json

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "dry_run"
    assert payload["evidence"]
    assert payload["argv"] == payload["evidence"]
