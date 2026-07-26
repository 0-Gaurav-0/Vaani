"""AgentRunner v2 — sessions, tools, brains, R3/R4 hard rule (T7.1)."""
from __future__ import annotations

import time

from vaani.brains.protocol import ToolCall
from vaani.exec.agent import AgentRunner, parse_agent_wake
from vaani.intent.schema import (
    Context,
    Intent,
    Result,
    RiskClass,
    SlotSpec,
    Status,
    Support,
    Verb,
)
from vaani.platform.protocol import PlatformId
from vaani.policy.confirm import ConfirmEngine
from vaani.verbs.registry import Registry
from tests.fakes.brain import FakeBrain, ScriptedBrain


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


def _verb(
    name: str,
    *,
    risk: RiskClass = RiskClass.R0,
    rung: int = 1,
) -> Verb:
    def handler(intent: Intent, context: Context) -> Result:
        _ = context
        return Result(
            status=Status.OK,
            summary=f"ran {intent.verb}",
            detail=f"ran {intent.verb} slots={dict(intent.slots)}",
            rung=rung,
        )

    return Verb(
        name=name,
        title=name,
        slots={"name": SlotSpec(type="str", required=False)},
        rung=rung,
        risk=risk,
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


def _registry(*verbs: Verb) -> Registry:
    registry = Registry()
    for verb in verbs:
        registry.register(verb)
    return registry


def test_fake_brain_raises_by_default() -> None:
    runner = AgentRunner(
        brains={"fake": FakeBrain()},
        default_brain="fake",
        registry=_registry(_verb("app.open")),
        platform=lambda: PlatformId.LINUX,
        progress_within=0,
    )
    result = runner.run("hello")
    assert result.returncode == -1
    assert "unexpected brain call" in result.stderr


def test_tools_exclude_agent_task_and_pass_catalog() -> None:
    brain = ScriptedBrain(text="done")
    registry = _registry(
        _verb("app.open"),
        _verb("agent.task", risk=RiskClass.R2, rung=6),
        _verb("project.test.run", risk=RiskClass.R1, rung=4),
    )
    runner = AgentRunner(
        brains={"codex": brain},
        registry=registry,
        platform=lambda: PlatformId.LINUX,
        progress_within=0,
    )
    out = runner.run("fix tests")
    assert out.stdout == "done"
    assert brain.calls
    tool_names = brain.calls[0]["tools"]
    assert "app.open" in tool_names
    assert "project.test.run" in tool_names
    assert "agent.task" not in tool_names


def test_session_continues_within_ttl() -> None:
    clock = {"t": 100.0}
    brain = ScriptedBrain(text="ok")
    runner = AgentRunner(
        brains={"codex": brain},
        registry=_registry(_verb("app.open")),
        platform=lambda: PlatformId.LINUX,
        session_ttl=600.0,
        clock=lambda: clock["t"],
        id_factory=lambda: "sess-a",
        progress_within=0,
    )
    first = runner.run("do one thing")
    assert first.session_id == "sess-a"
    clock["t"] = 200.0  # still within 10 min
    ids = iter(["sess-b"])
    runner._id_factory = lambda: next(ids)
    second = runner.run("and now also push it")
    assert second.session_id == "sess-a"
    assert runner.session is not None
    assert runner.session.brain == "codex"


def test_session_spawns_after_ttl() -> None:
    clock = {"t": 0.0}
    ids = iter(["s1", "s2"])
    brain = ScriptedBrain(text="ok")
    runner = AgentRunner(
        brains={"codex": brain},
        registry=_registry(_verb("app.open")),
        platform=lambda: PlatformId.LINUX,
        session_ttl=600.0,
        clock=lambda: clock["t"],
        id_factory=lambda: next(ids),
        progress_within=0,
    )
    assert runner.run("first").session_id == "s1"
    clock["t"] = 601.0
    assert runner.run("second").session_id == "s2"


def test_progress_within_one_second() -> None:
    progress: list[str] = []
    brain = ScriptedBrain(text="slow-ok", delay=0.15)
    runner = AgentRunner(
        brains={"codex": brain},
        registry=_registry(),
        platform=lambda: PlatformId.LINUX,
        progress_within=0.05,
        on_progress=progress.append,
    )
    runner.run("x")
    assert progress
    assert any("working" in p or "starting" in p for p in progress)


def test_redaction_on_brain_stderr() -> None:
    brain = ScriptedBrain(text="ok", redact_probe="api_key=supersecret")
    runner = AgentRunner(
        brains={"codex": brain},
        registry=_registry(),
        platform=lambda: PlatformId.LINUX,
        progress_within=0,
    )
    result = runner.run("x")
    assert "supersecret" not in result.stderr
    assert "REDACTED" in result.stderr


def test_cancel_marks_result_cancelled() -> None:
    brain = ScriptedBrain(text="nope", delay=0.3)
    runner = AgentRunner(
        brains={"codex": brain},
        registry=_registry(),
        platform=lambda: PlatformId.LINUX,
        progress_within=0,
    )

    def _cancel_soon() -> None:
        time.sleep(0.05)
        runner.cancel()

    import threading

    threading.Thread(target=_cancel_soon, daemon=True).start()
    result = runner.run("long")
    assert result.cancelled


def test_agent_cannot_approve_r3_via_agent() -> None:
    registry = _registry(_verb("git.push.force", risk=RiskClass.R3, rung=4))
    confirm = ConfirmEngine(id_factory=lambda: "p1")
    runner = AgentRunner(
        brains={"codex": ScriptedBrain()},
        registry=registry,
        confirm=confirm,
        platform=lambda: PlatformId.LINUX,
        get_context=_context,
        progress_within=0,
    )
    result = runner.invoke_tool("git.push.force", {}, via="agent")
    assert result.status is Status.REFUSED
    assert "cannot approve" in result.summary
    assert confirm.peek() is None


def test_agent_cannot_approve_r4_via_agent() -> None:
    registry = _registry(_verb("shell.raw", risk=RiskClass.R4, rung=7))
    confirm = ConfirmEngine(id_factory=lambda: "p2")
    runner = AgentRunner(
        brains={"codex": ScriptedBrain()},
        registry=registry,
        confirm=confirm,
        platform=lambda: PlatformId.LINUX,
        get_context=_context,
        progress_within=0,
    )
    result = runner.invoke_tool("shell.raw", {}, via="agent")
    assert result.status is Status.REFUSED
    assert "R4" in result.detail


def test_agent_tool_call_r3_refused_during_run() -> None:
    brain = ScriptedBrain(
        text="tried force push",
        tool_calls=(ToolCall(name="git.push.force", slots={}),),
    )
    registry = _registry(_verb("git.push.force", risk=RiskClass.R3, rung=4))
    confirm = ConfirmEngine(id_factory=lambda: "p3")
    runner = AgentRunner(
        brains={"codex": brain},
        registry=registry,
        confirm=confirm,
        platform=lambda: PlatformId.LINUX,
        get_context=_context,
        progress_within=0,
    )
    out = runner.run("force push please")
    assert out.stdout == "tried force push"
    assert out.tool_results
    assert out.tool_results[0].status is Status.REFUSED
    assert "cannot approve" in out.stderr


def test_agent_can_approve_r2_via_agent() -> None:
    registry = _registry(_verb("app.quit", risk=RiskClass.R2, rung=1))
    confirm = ConfirmEngine(id_factory=lambda: "p4")
    runner = AgentRunner(
        brains={"codex": ScriptedBrain()},
        registry=registry,
        confirm=confirm,
        platform=lambda: PlatformId.LINUX,
        get_context=_context,
        progress_within=0,
    )
    result = runner.invoke_tool("app.quit", {"name": "Slack"}, via="agent")
    assert result.status is Status.OK


def test_brain_selection_claude_cursor() -> None:
    brains = {
        "codex": ScriptedBrain(name="codex", text="from-codex"),
        "claude": ScriptedBrain(name="claude", text="from-claude"),
        "cursor": ScriptedBrain(name="cursor", text="from-cursor"),
    }
    runner = AgentRunner(
        brains=brains,
        registry=_registry(),
        platform=lambda: PlatformId.LINUX,
        progress_within=0,
    )
    assert runner.run("x", brain="claude").stdout == "from-claude"
    assert runner.run("x", brain="cursor").brain == "cursor"


def test_parse_agent_wake_phrases() -> None:
    assert parse_agent_wake("Vaani, agent: fix the failing test") == (
        "fix the failing test",
        None,
    )
    assert parse_agent_wake("agent: refactor this module") == (
        "refactor this module",
        None,
    )
    assert parse_agent_wake("use Claude for this: draft a reply") == (
        "draft a reply",
        "claude",
    )
    assert parse_agent_wake("use Codex for this rewrite the README") == (
        "rewrite the README",
        "codex",
    )
    assert parse_agent_wake("use Cursor for this") == ("", "cursor")
    assert parse_agent_wake(
        "Vaani, agent: use Claude for this: fix flaky test"
    ) == ("fix flaky test", "claude")
    assert parse_agent_wake("open Terminal") is None
    assert parse_agent_wake("I use Claude") is None
