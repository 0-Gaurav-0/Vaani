"""Wake-phrase routing for rung-6 agent opt-in (T7.1 / spec §12.5)."""
from __future__ import annotations

from vaani.apps import launch_app, resolve_app
from vaani.intent.router import Router
from vaani.platform.protocol import PlatformId
from vaani.sites import resolve_site
from vaani.verbs.packs.core import build_core_registry


def _router() -> Router:
    from vaani.policy.undo import UndoStack, register_undo
    from vaani.verbs.packs.registry import register_stub_packs

    registry, patterns = build_core_registry(
        resolve_app_fn=resolve_app,
        launch_app_fn=launch_app,
        resolve_site_fn=resolve_site,
        open_browser_fn=lambda **_k: "Opened browser.",
    )
    patterns = patterns + register_undo(registry, UndoStack())
    patterns = patterns + register_stub_packs(registry)
    return Router(
        registry,
        patterns,
        resolve_app=resolve_app,
        resolve_site=resolve_site,
    )


def test_vaani_agent_wake_forces_rung_6() -> None:
    intent = _router().route(
        "Vaani, agent: fix the failing test",
        platform=PlatformId.LINUX,
    )
    assert intent is not None
    assert intent.verb == "agent.task"
    assert intent.rung == 6
    assert intent.source == "wake"
    assert intent.slots["prompt"] == "fix the failing test"
    assert intent.brain is None


def test_use_claude_for_this_selects_brain() -> None:
    intent = _router().route(
        "use Claude for this: draft a reply",
        platform=PlatformId.LINUX,
    )
    assert intent is not None
    assert intent.verb == "agent.task"
    assert intent.brain == "claude"
    assert intent.slots["brain"] == "claude"
    assert intent.slots["prompt"] == "draft a reply"


def test_combined_wake_and_brain() -> None:
    intent = _router().route(
        "Vaani, agent: use Cursor for this rewrite helpers",
        platform=PlatformId.MACOS,
    )
    assert intent is not None
    assert intent.verb == "agent.task"
    assert intent.brain == "cursor"
    assert intent.slots["prompt"] == "rewrite helpers"


def test_wake_skips_app_resolver() -> None:
    """Wake phrase must not be stolen by app.open for 'Claude'."""
    intent = _router().route(
        "Vaani, agent: open Claude",
        platform=PlatformId.LINUX,
    )
    assert intent is not None
    assert intent.verb == "agent.task"
    assert intent.slots["prompt"] == "open Claude"
