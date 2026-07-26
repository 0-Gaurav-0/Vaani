"""Unit tests for intent-stack core contracts (T0.3)."""
from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from vaani.intent.schema import (
    AgentSession,
    Context,
    FocusInfo,
    Intent,
    OverlayOp,
    PendingAction,
    ProjectProfile,
    RepoInfo,
    Result,
    RiskClass,
    ScreenFrame,
    SlotSpec,
    Status,
    Support,
    UndoToken,
    Verb,
)
from vaani.platform.protocol import PlatformId


def _sample_intent(**overrides: object) -> Intent:
    base: dict[str, object] = {
        "verb": "app.open",
        "slots": {"name": "Terminal"},
        "rung": 1,
        "confidence": 1.0,
        "source": "grammar",
        "mode": "act",
        "utterance": "open terminal",
        "raw_utterance": "Open Terminal",
        "modifiers": frozenset(),
        "brain": None,
    }
    base.update(overrides)
    return Intent(**base)  # type: ignore[arg-type]


def _sample_context(**overrides: object) -> Context:
    base: dict[str, object] = {
        "platform": PlatformId.MACOS,
        "workspace": Path("/tmp/proj"),
        "workspace_source": "config",
        "repo": None,
        "project": None,
        "focus": None,
        "screen": None,
        "session": None,
    }
    base.update(overrides)
    return Context(**base)  # type: ignore[arg-type]


def _ok_handler(intent: Intent, context: Context) -> Result:
    return Result(status=Status.OK, summary=f"ran {intent.verb}")


def test_intent_construction() -> None:
    intent = _sample_intent(modifiers=frozenset({"dry_run"}))
    assert intent.verb == "app.open"
    assert intent.slots["name"] == "Terminal"
    assert intent.rung == 1
    assert intent.modifiers == frozenset({"dry_run"})
    assert intent.brain is None


def test_context_and_related_types() -> None:
    repo = RepoInfo(root=Path("/tmp/proj"), branch="main", dirty=True)
    project = ProjectProfile(
        root=Path("/tmp/proj"),
        manager="uv",
        test=("pytest",),
    )
    focus = FocusInfo(app_id="com.apple.Terminal", window_title="zsh")
    screen = ScreenFrame(width=1920, height=1080, data=b"\x00")
    session = AgentSession(
        id="s1", brain="codex", started_at=1.0, last_active_at=2.0
    )
    ctx = _sample_context(
        repo=repo,
        project=project,
        focus=focus,
        screen=screen,
        session=session,
        workspace_source="focused-editor",
    )
    assert ctx.platform is PlatformId.MACOS
    assert ctx.repo is not None and ctx.repo.branch == "main"
    assert ctx.project is not None and ctx.project.test == ("pytest",)
    assert ctx.focus is not None and ctx.focus.app_id == "com.apple.Terminal"
    assert ctx.screen is not None and ctx.screen.width == 1920
    assert ctx.session is not None and ctx.session.brain == "codex"


def test_verb_construction() -> None:
    verb = Verb(
        name="app.open",
        title="Open an application",
        slots={"name": SlotSpec(type="str", required=True)},
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
        handler=_ok_handler,
    )
    intent = _sample_intent()
    result = verb.handler(intent, _sample_context())
    assert verb.risk is RiskClass.R0
    assert result.status is Status.OK
    assert result.summary == "ran app.open"


def test_result_with_pending_undo_overlay() -> None:
    pending = PendingAction(
        id="p1",
        verb="system.port.free",
        slots={"port": 3000},
        materialized=("kill", "41233"),
        risk=RiskClass.R2,
        expires_at=100.0,
    )
    undo = UndoToken(
        verb="system.volume.set",
        inverse_verb="system.volume.set",
        slots={"level": 50},
        expires_at=200.0,
    )
    overlay = (
        OverlayOp(kind="point", x=10.0, y=20.0, label="Export"),
        OverlayOp(kind="caption", x=10.0, y=40.0, text="Click here"),
    )
    result = Result(
        status=Status.NEEDS_CONFIRM,
        summary="Free port 3000?",
        detail="Will SIGTERM node PID 41233",
        evidence=("lsof", "-iTCP:3000"),
        rung=2,
        undo=undo,
        pending=pending,
        overlay=overlay,
    )
    assert result.pending is pending
    assert result.undo is undo
    assert result.overlay[0].kind == "point"
    assert result.status is Status.NEEDS_CONFIRM


def test_immutability() -> None:
    intent = _sample_intent()
    result = Result(status=Status.OK, summary="ok")
    verb = Verb(
        name="app.open",
        title="Open an application",
        slots={},
        rung=1,
        risk=RiskClass.R0,
        requires=frozenset(),
        support={PlatformId.MACOS: Support.SUPPORTED},
        undo=None,
        pack="core",
        handler=_ok_handler,
    )
    with pytest.raises(FrozenInstanceError):
        intent.verb = "other"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        result.summary = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        verb.name = "other"  # type: ignore[misc]


def test_summary_truncation() -> None:
    long = "x" * 120
    result = Result(status=Status.OK, summary=long)
    assert len(result.summary) == 80
    assert result.summary == long[:80]

    exact = "y" * 80
    assert Result(status=Status.OK, summary=exact).summary == exact

    short = "ok"
    assert Result(status=Status.OK, summary=short).summary == short


def test_status_round_trip_through_str() -> None:
    for status in Status:
        assert Status(str(status.value)) is status
        assert Status(status.value) is status
        assert status.value == str(status.value)


def test_schema_imports_only_platform_id() -> None:
    path = Path(__file__).resolve().parents[3] / "src" / "vaani" / "intent" / "schema.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    vaani_imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith(
            "vaani"
        ):
            vaani_imports.append(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "vaani" or alias.name.startswith("vaani."):
                    vaani_imports.append(alias.name)
    assert vaani_imports == ["vaani.platform.protocol"]
