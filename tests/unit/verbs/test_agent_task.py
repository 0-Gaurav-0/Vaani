"""T7.2 agent.task: failing-job seed, diff-before-apply confirm, force-push gate."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from vaani.intent.schema import (
    Context,
    FocusInfo,
    Intent,
    RiskClass,
    Status,
)
from vaani.platform.protocol import PlatformId
from vaani.policy.confirm import ConfirmEngine
from vaani.verbs.agent_context import (
    extract_unified_diff,
    last_failing_run,
    seed_agent_prompt,
)
from vaani.verbs.packs.core import build_core_verbs
from vaani.verbs.packs.project import LastRun


def _context(
    *,
    root: Path | None = None,
    document: Path | None = None,
    selection: str | None = None,
) -> Context:
    focus = None
    if document is not None or selection is not None:
        focus = FocusInfo(
            app_id="editor",
            window_title="file",
            document_path=document,
            selection=selection,
        )
    return Context(
        platform=PlatformId.LINUX,
        workspace=root,
        workspace_source="test",
        repo=None,
        project=None,
        focus=focus,
        screen=None,
        session=None,
    )


def _intent(
    prompt: str = "fix the failing test",
    *,
    modifiers: frozenset[str] = frozenset(),
    **slots: object,
) -> Intent:
    return Intent(
        verb="agent.task",
        slots={"prompt": prompt, **slots},
        rung=6,
        confidence=1.0,
        source="test",
        mode="act",
        utterance=prompt,
        raw_utterance=prompt,
        modifiers=modifiers,
        brain=None,
    )


def _verbs(
    *,
    last_runs: list[LastRun] | None = None,
    runner: object | None = None,
    get_supervisor=None,
):
    runs = last_runs if last_runs is not None else []
    codex = runner

    def get_codex():
        return codex

    verbs = {
        v.name: v
        for v in build_core_verbs(
            resolve_app_fn=lambda _t: None,
            launch_app_fn=lambda _t: "",
            resolve_site_fn=lambda _t: None,
            open_browser_fn=lambda **_k: "",
            get_codex=get_codex if codex is not None else None,
            get_supervisor=get_supervisor,
            last_runs=runs,
        )
    }
    return verbs, runs


def test_failing_job_context_seeded_in_confirm(tmp_path: Path) -> None:
    fail_log = "FAILED tests/unit/test_foo.py::test_bar - AssertionError\n"
    last = [
        LastRun(
            key="project.test",
            verb="project.test.run",
            argv=("pytest",),
            cwd=str(tmp_path),
            returncode=1,
            log_text=fail_log,
        )
    ]
    verbs, _ = _verbs(last_runs=last)
    result = verbs["agent.task"].handler(
        _intent("fix the failing test"),
        _context(root=tmp_path),
    )
    assert result.status is Status.NEEDS_CONFIRM
    assert result.pending is not None
    assert result.pending.risk is RiskClass.R2
    assert "test_foo.py" in result.detail
    assert "AssertionError" in result.detail
    assert result.pending.slots.get("failing_job") == "project.test"
    assert "test_foo.py" in str(result.pending.slots.get("seeded_prompt") or "")


def test_confirm_required_before_agent_runs(tmp_path: Path) -> None:
    calls: list[str] = []

    class Runner:
        def run(self, prompt: str):
            calls.append(prompt)
            return SimpleNamespace(
                stdout="ok", stderr="", returncode=0, cancelled=False, timed_out=False
            )

    verbs, _ = _verbs(runner=Runner())
    first = verbs["agent.task"].handler(
        _intent("refactor this file"),
        _context(root=tmp_path, document=tmp_path / "a.py"),
    )
    assert first.status is Status.NEEDS_CONFIRM
    assert calls == []

    second = verbs["agent.task"].handler(
        _intent(
            "refactor this file",
            modifiers=frozenset({"confirmed"}),
            seeded_prompt=first.pending.slots["seeded_prompt"],
        ),
        _context(root=tmp_path, document=tmp_path / "a.py"),
    )
    assert second.status is Status.OK
    assert len(calls) == 1
    assert "Focused file:" in calls[0]


def test_diff_shown_before_apply(tmp_path: Path) -> None:
    diff = (
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -1,1 +1,2 @@\n"
        " x = 1\n"
        "+y = 2\n"
    )

    class Runner:
        def run(self, prompt: str):
            return SimpleNamespace(
                stdout=f"Here is the patch:\n{diff}",
                stderr="",
                returncode=0,
                cancelled=False,
                timed_out=False,
            )

    verbs, _ = _verbs(runner=Runner())
    result = verbs["agent.task"].handler(
        _intent("add logging", modifiers=frozenset({"confirmed"})),
        _context(root=tmp_path),
    )
    assert result.status is Status.NEEDS_CONFIRM
    assert result.pending is not None
    assert "--- a/foo.py" in result.detail
    assert result.pending.slots.get("proposed_diff")
    assert "apply-diff" in result.evidence

    applied = verbs["agent.task"].handler(
        _intent(
            "add logging",
            modifiers=frozenset({"confirmed"}),
            proposed_diff=result.pending.slots["proposed_diff"],
        ),
        _context(root=tmp_path),
    )
    assert applied.status is Status.OK
    assert "Applied" in applied.summary


def test_seed_from_supervisor_when_memory_empty() -> None:
    class Job:
        key = "project.test"
        verb = "project.test.run"
        argv = ("pytest",)
        cwd = "/tmp"
        status = "stopped"
        started_at = 1.0

    class Supervisor:
        def list_jobs(self):
            return (Job(),)

        def logs(self, key: str, *, tail: int = 200):
            assert key == "project.test"
            return "FAILED tests/x.py::test_y\n"

    failing = last_failing_run([], get_supervisor=lambda: Supervisor())
    assert failing is not None
    assert "test_y" in failing.log_text

    seed = seed_agent_prompt("fix it", failing=failing)
    assert seed.seeded
    assert "test_y" in seed.prompt


def test_extract_unified_diff() -> None:
    assert extract_unified_diff("no patch here") is None
    body = "note\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n"
    assert extract_unified_diff(body) is not None
    assert extract_unified_diff(body).startswith("--- a/x")


def test_brain_cannot_self_approve_force_push() -> None:
    """S7 gate: ConfirmEngine rejects via=agent for R3 force push."""
    from vaani.intent.schema import Result as VerbResult
    from vaani.intent.schema import Support, Verb

    def handler(intent, context):
        return VerbResult(status=Status.OK, summary="pushed", rung=4)

    force = Verb(
        name="vcs.push.force",
        title="Force push",
        slots={},
        rung=4,
        risk=RiskClass.R3,
        requires=frozenset(),
        support={
            PlatformId.LINUX: Support.SUPPORTED,
            PlatformId.MACOS: Support.SUPPORTED,
            PlatformId.WINDOWS: Support.SUPPORTED,
        },
        undo=None,
        pack="git",
        handler=handler,
    )
    engine = ConfirmEngine(id_factory=lambda: "fp1")
    intent = Intent(
        verb="vcs.push.force",
        slots={},
        rung=4,
        confidence=1.0,
        source="agent",
        mode="act",
        utterance="force push",
        raw_utterance="force push",
        modifiers=frozenset(),
        brain="codex",
    )
    engine.stage(
        intent,
        force,
        ("git", "push", "--force-with-lease"),
        context=_context(),
    )
    assert engine.approve("fp1", via="agent") is None
    assert engine.peek() is not None
    assert engine.approve("fp1", via="pill") is not None
