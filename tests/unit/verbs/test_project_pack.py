"""Unit tests for T3.4 project/job/editor pack."""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

from vaani.config import Settings
from vaani.context.project import clear_project_cache
from vaani.exec.runner import Completed, Command
from vaani.exec.supervisor import Supervisor
from vaani.intent.grammar import match
from vaani.intent.schema import Context, Intent, ProjectProfile, Result, Status
from vaani.platform.protocol import PlatformId, ProcInfo
from vaani.verbs.packs.project import (
    PROJECT_VERB_NAMES,
    LastRun,
    build_project_verbs,
    parse_failing_names,
    parse_file_line,
    project_patterns,
)


class _FakeSystem:
    def __init__(self) -> None:
        self.listeners: dict[int, tuple[ProcInfo, ...]] = {}
        self.calls: list[tuple[str, object]] = []
        self.kill_result = Result(status=Status.OK, summary="Signaled", rung=2)

    def list_listeners(self, port: int) -> tuple[ProcInfo, ...]:
        self.calls.append(("list_listeners", port))
        return self.listeners.get(int(port), ())

    def kill_pids(self, pids, *, signal: str = "term"):
        self.calls.append(("kill_pids", (tuple(pids), signal)))
        return self.kill_result


def _intent(
    verb: str,
    slots: dict | None = None,
    *,
    modifiers: frozenset[str] = frozenset(),
    utterance: str | None = None,
) -> Intent:
    text = utterance or verb
    return Intent(
        verb=verb,
        slots=slots or {},
        rung=3,
        confidence=1.0,
        source="test",
        mode="act",
        utterance=text,
        raw_utterance=text,
        modifiers=modifiers,
        brain=None,
    )


def _context(
    tmp_path: Path | None = None,
    *,
    project: ProjectProfile | None = None,
    platform: PlatformId = PlatformId.MACOS,
) -> Context:
    workspace = tmp_path
    return Context(
        platform=platform,
        workspace=workspace,
        workspace_source="test",
        repo=None,
        project=project,
        focus=None,
        screen=None,
        session=None,
    )


def _settings(tmp_path: Path) -> Settings:
    settings = Settings.from_home(tmp_path / "home", platform=sys.platform)
    settings.prepare()
    return settings


def _verbs(
    *,
    supervisor: Supervisor | None = None,
    system: _FakeSystem | None = None,
    run_fn=None,
    last_runs: list[LastRun] | None = None,
    cancel: threading.Event | None = None,
    popen=None,
):
    return {
        v.name: v
        for v in build_project_verbs(
            get_supervisor=lambda: supervisor,
            get_system=lambda: system,
            get_platform=lambda: PlatformId.MACOS,
            get_cancel=lambda: cancel,
            run_fn=run_fn,
            popen=popen,
            last_runs=last_runs,
        )
    }


def test_project_verb_names() -> None:
    verbs = _verbs()
    assert set(verbs) == PROJECT_VERB_NAMES


def test_profile_miss_refuses_with_checked_files(tmp_path: Path) -> None:
    clear_project_cache()
    empty = tmp_path / "empty"
    empty.mkdir()
    verbs = _verbs()
    result = verbs["project.test.run"].handler(
        _intent("project.test.run"),
        _context(empty),
    )
    assert result.status is Status.REFUSED
    assert "Checked" in result.detail or "checked" in result.detail.casefold()
    assert "package.json" in result.detail or "package.json" in "".join(result.evidence)


def test_test_run_pass_and_fail_summary(tmp_path: Path) -> None:
    clear_project_cache()
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    tests = repo / "tests"
    tests.mkdir()
    (tests / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    (tests / "test_bad.py").write_text(
        "def test_bad():\n    assert False\n", encoding="utf-8"
    )

    calls: list[Command] = []

    def fake_run(cmd: Command, *, cancel=None) -> Completed:
        calls.append(cmd)
        argv_s = " ".join(cmd.argv)
        if "test_bad" in "".join(p.name for p in (repo / "tests").iterdir()) and "-k" not in cmd.argv:
            # Full suite: fail
            return Completed(
                argv=cmd.argv,
                returncode=1,
                stdout="FAILED tests/test_bad.py::test_bad - AssertionError\n",
                stderr="",
            )
        if "test_ok" in argv_s or (cmd.argv[-1:] == ("tests/test_ok.py",)):
            return Completed(argv=cmd.argv, returncode=0, stdout="1 passed\n", stderr="")
        return Completed(
            argv=cmd.argv,
            returncode=1,
            stdout="FAILED tests/test_bad.py::test_bad\n",
            stderr="",
        )

    # Passing filtered run
    def pass_run(cmd: Command, *, cancel=None) -> Completed:
        calls.append(cmd)
        return Completed(
            argv=cmd.argv,
            returncode=0,
            stdout="===== 1 passed in 0.01s =====\n",
            stderr="",
        )

    verbs = _verbs(run_fn=pass_run)
    profile = ProjectProfile(root=repo, manager="pip", test=("pytest",))
    ok = verbs["project.test.run"].handler(
        _intent("project.test.run", {"scope": "all"}),
        _context(repo, project=profile),
    )
    assert ok.status is Status.OK
    assert "exit 0" in ok.summary.casefold() or "passed" in ok.summary.casefold()
    assert calls and calls[0].argv[0] == "pytest"

    fails: list[LastRun] = []

    def fail_run(cmd: Command, *, cancel=None) -> Completed:
        return Completed(
            argv=cmd.argv,
            returncode=1,
            stdout="FAILED tests/test_bad.py::test_bad - AssertionError\n1 failed\n",
            stderr="",
        )

    verbs = _verbs(run_fn=fail_run, last_runs=fails)
    bad = verbs["project.test.run"].handler(
        _intent("project.test.run"),
        _context(repo, project=profile),
    )
    assert bad.status is Status.FAILED
    assert "exit 1" in bad.summary or "1" in bad.summary
    assert "test_bad" in bad.detail
    assert fails and fails[-1].returncode == 1


def test_cancel_mid_run(tmp_path: Path) -> None:
    clear_project_cache()
    repo = tmp_path / "repo"
    repo.mkdir()
    profile = ProjectProfile(root=repo, manager="pip", test=("pytest",))
    cancel = threading.Event()

    def slow_run(cmd: Command, *, cancel=None) -> Completed:
        assert cancel is not None
        # Simulate cancel arriving mid-run.
        cancel.set()
        return Completed(
            argv=cmd.argv,
            returncode=-1,
            stdout="",
            stderr="",
            cancelled=True,
        )

    verbs = _verbs(run_fn=slow_run, cancel=cancel)
    result = verbs["project.test.run"].handler(
        _intent("project.test.run"),
        _context(repo, project=profile),
    )
    assert result.status is Status.FAILED
    assert "cancel" in result.summary.casefold()


def test_dev_start_stop_via_supervisor(tmp_path: Path) -> None:
    clear_project_cache()
    settings = _settings(tmp_path)
    sup = Supervisor(settings)
    script = tmp_path / "serve.sh"
    script.write_text(
        "#!/bin/sh\necho 'ready at http://127.0.0.1:8765'\nwhile true; do sleep 1; done\n"
    )
    script.chmod(0o755)
    profile = ProjectProfile(
        root=tmp_path,
        manager=None,
        dev=(str(script),),
    )
    verbs = _verbs(supervisor=sup)
    started = verbs["project.dev.start"].handler(
        _intent("project.dev.start"),
        _context(tmp_path, project=profile),
    )
    try:
        assert started.status is Status.OK
        assert "Started" in started.summary or "already" in started.summary.casefold()
        listed = verbs["job.list"].handler(_intent("job.list"), _context(tmp_path))
        assert listed.status is Status.OK
        assert "project.dev" in listed.detail
        logs = verbs["job.logs"].handler(
            _intent("job.logs", {"key": "project.dev"}),
            _context(tmp_path),
        )
        assert logs.status is Status.OK
        stopped = verbs["project.dev.stop"].handler(
            _intent("project.dev.stop"),
            _context(tmp_path, project=profile),
        )
        assert stopped.status is Status.OK
    finally:
        if sup.status("project.dev") is not None:
            sup.stop("project.dev", forceful=True)


def test_dev_stop_unmanaged_port_needs_confirm(tmp_path: Path) -> None:
    system = _FakeSystem()
    system.listeners[3000] = (ProcInfo(pid=4242, name="node", uid=501),)
    verbs = _verbs(supervisor=Supervisor(_settings(tmp_path)), system=system)
    result = verbs["project.dev.stop"].handler(
        _intent("project.dev.stop"),
        _context(tmp_path),
    )
    assert result.status is Status.NEEDS_CONFIRM
    assert result.pending is not None
    assert result.pending.risk.value == "R2"
    assert "4242" in result.detail
    assert not any(c[0] == "kill_pids" for c in system.calls)

    confirmed = verbs["project.dev.stop"].handler(
        _intent(
            "project.dev.stop",
            {"port": 3000},
            modifiers=frozenset({"confirmed"}),
        ),
        _context(tmp_path),
    )
    assert confirmed.status is Status.OK
    assert ("kill_pids", ((4242,), "term")) in system.calls


def test_editor_open_from_failing_job_log(tmp_path: Path) -> None:
    last: list[LastRun] = [
        LastRun(
            key="project.test",
            verb="project.test.run",
            argv=("pytest",),
            cwd=str(tmp_path),
            returncode=1,
            log_text="FAILED tests/unit/test_foo.py:42 AssertionError\n",
        )
    ]
    spawned: list[list[str]] = []

    def fake_popen(argv, **_kwargs):
        spawned.append(list(argv))

        class _P:
            def poll(self):
                return None

        return _P()

    verbs = _verbs(last_runs=last, popen=fake_popen)
    # Pretend cursor CLI exists via which monkeypatch
    import vaani.verbs.packs.project as project_mod

    real_which = project_mod.shutil.which

    def fake_which(name: str):
        if name in {"cursor", "code"}:
            return f"/usr/bin/{name}"
        return real_which(name)

    project_mod.shutil.which = fake_which  # type: ignore[assignment]
    try:
        result = verbs["editor.open"].handler(
            _intent(
                "editor.open",
                {"from_error": True},
                utterance="open the file with the build error",
            ),
            _context(tmp_path),
        )
    finally:
        project_mod.shutil.which = real_which  # type: ignore[assignment]

    assert result.status is Status.OK
    assert "test_foo.py" in result.summary or "test_foo.py" in result.detail
    assert spawned
    assert any("42" in " ".join(a) or "test_foo.py" in " ".join(a) for a in spawned)


def test_parse_helpers() -> None:
    names = parse_failing_names(
        "FAILED tests/a.py::test_a - boom\nFAILED tests/b.py::test_b\n"
    )
    assert "tests/a.py::test_a" in names
    assert parse_file_line("Error in src/main.ts:12: typo") == ("src/main.ts", 12)


def test_patterns_cover_canonical_utterances() -> None:
    patterns = project_patterns()
    assert match("start the dev server", patterns)[0] == "project.dev.start"
    assert match("run the tests", patterns)[0] == "project.test.run"
    assert match("what's running", patterns)[0] == "job.list"
    assert match("open the file with the build error", patterns)[0] == "editor.open"
    # Must not steal bare app.open "open Cursor"
    hit = match("open cursor", patterns)
    assert hit is None or hit[0] != "editor.open"


def test_dry_run_does_not_start(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    sup = Supervisor(settings)
    profile = ProjectProfile(root=tmp_path, dev=("echo", "hi"))
    verbs = _verbs(supervisor=sup)
    result = verbs["project.dev.start"].handler(
        _intent("project.dev.start", modifiers=frozenset({"dry_run"})),
        _context(tmp_path, project=profile),
    )
    assert result.status is Status.DRY_RUN
    assert sup.status("project.dev") is None


@pytest.mark.parametrize(
    "field,verb",
    [
        ("build", "project.build"),
        ("typecheck", "project.typecheck"),
    ],
)
def test_missing_command_field_refuses(tmp_path: Path, field: str, verb: str) -> None:
    clear_project_cache()
    # Manifest present but no build/typecheck scripts.
    (tmp_path / "package.json").write_text(
        '{"name":"x","scripts":{"test":"jest"}}',
        encoding="utf-8",
    )
    verbs = _verbs()
    result = verbs[verb].handler(_intent(verb), _context(tmp_path))
    assert result.status is Status.REFUSED
    assert field in result.detail.casefold() or "checked" in result.detail.casefold()
