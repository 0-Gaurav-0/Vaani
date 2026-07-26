"""Unit tests for the git / VCS pack (T4.2)."""
from __future__ import annotations

from pathlib import Path

import pytest

from vaani.intent.grammar import match
from vaani.intent.schema import (
    Context,
    FocusInfo,
    Intent,
    RepoInfo,
    Status,
)
from vaani.platform.protocol import PlatformId
from vaani.policy.dryrun import materialize_argv
from vaani.verbs.packs.git import (
    GIT_VERB_NAMES,
    build_git_verbs,
    git_patterns,
    materialize_git_argv,
)


def _intent(
    verb: str,
    slots: dict | None = None,
    *,
    modifiers: frozenset[str] = frozenset(),
) -> Intent:
    return Intent(
        verb=verb,
        slots=slots or {},
        rung=4,
        confidence=1.0,
        source="test",
        mode="act",
        utterance=verb,
        raw_utterance=verb,
        modifiers=modifiers,
        brain=None,
    )


def _context(
    root: Path | None = None,
    *,
    dirty: bool = False,
    document: Path | None = None,
) -> Context:
    repo = None
    if root is not None:
        repo = RepoInfo(root=root, branch="main", dirty=dirty)
    focus = None
    if document is not None:
        focus = FocusInfo(document_path=document)
    return Context(
        platform=PlatformId.LINUX,
        workspace=root,
        workspace_source="test",
        repo=repo,
        project=None,
        focus=focus,
        screen=None,
        session=None,
    )


def _verbs(**kwargs):
    return {v.name: v for v in build_git_verbs(**kwargs)}


def test_git_verb_names() -> None:
    assert set(_verbs()) == GIT_VERB_NAMES


def test_patterns_cover_canonical_utterances() -> None:
    patterns = git_patterns()
    assert match("create a new branch called assistant use cases", patterns)[0] == (
        "vcs.branch.create"
    )
    assert match("new branch feature slash port killer", patterns)[0] == (
        "vcs.branch.create"
    )
    assert match("switch to main", patterns)[0] == "vcs.checkout"
    assert match("pull latest", patterns)[0] == "vcs.pull"
    assert match("commit my changes with a reasonable message", patterns)[0] == (
        "vcs.commit"
    )
    assert match("force push", patterns)[0] == "vcs.push.force"
    assert match("stash my changes", patterns)[0] == "vcs.stash.push"
    assert match("pop the stash", patterns)[0] == "vcs.stash.pop"
    assert match("show the diff", patterns)[0] == "vcs.diff.show"
    assert match("discard the changes in this file", patterns)[0] == "vcs.restore"


def test_switch_to_main_does_not_match_branch_create() -> None:
    patterns = git_patterns()
    hit = match("switch to main", patterns)
    assert hit is not None
    assert hit[0] == "vcs.checkout"


def test_no_bare_force_in_pack_source() -> None:
    src = Path(__file__).resolve().parents[3] / "src" / "vaani" / "verbs" / "packs" / "git.py"
    text = src.read_text(encoding="utf-8")
    # Ban argv token "--force" that is not part of "--force-with-lease".
    for line in text.splitlines():
        if "--force" in line and "--force-with-lease" not in line:
            # Allow comments that say "never bare --force"
            if "never" in line.casefold() or line.lstrip().startswith("#"):
                continue
            pytest.fail(f"bare --force in pack source: {line}")


def test_materialize_force_push_is_lease_only() -> None:
    argv = materialize_git_argv("vcs.push.force", {})
    assert argv is not None
    assert "--force-with-lease" in argv
    assert "--force" not in argv  # exact token absent


def test_materialize_argv_dryrun_shapes() -> None:
    ctx = _context(Path("/tmp/repo"))
    for verb in sorted(GIT_VERB_NAMES):
        slots: dict = {}
        if verb == "vcs.branch.create":
            slots = {"name": "assistant-use-cases"}
        elif verb == "vcs.checkout":
            slots = {"ref": "main"}
        elif verb == "vcs.branch.delete":
            slots = {"name": "tmp"}
        elif verb == "vcs.commit":
            slots = {"message": "Update files"}
        elif verb == "vcs.restore":
            slots = {"path": "a.txt"}
        argv = materialize_argv(
            verb, slots, platform=PlatformId.LINUX, context=ctx
        )
        assert argv is not None, verb
        assert argv[0] == "git", verb
        assert argv[1] == "-C", verb
        assert "--force" not in argv or "--force-with-lease" in argv


def test_commit_needs_confirm_shows_message(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    # Minimal fake runner: dirty tree + commit message path.
    calls: list[tuple[str, ...]] = []

    class Done:
        def __init__(self, code=0, stdout="", stderr=""):
            self.returncode = code
            self.stdout = stdout
            self.stderr = stderr
            self.timed_out = False
            self.cancelled = False
            self.argv = ()

    def run_fn(cmd):
        calls.append(cmd.argv)
        argv = cmd.argv
        if "status" in argv and "--porcelain" in argv:
            return Done(0, " M file.txt\n")
        if "diff" in argv and "--cached" in argv and "--quiet" in argv:
            return Done(1)
        if "commit" in argv:
            return Done(0, "[main abc] ok\n")
        if "add" in argv:
            return Done(0)
        return Done(0)

    verbs = _verbs(run_fn=run_fn, which=lambda _b: "/usr/bin/git")
    result = verbs["vcs.commit"].handler(
        _intent("vcs.commit"),
        _context(root, dirty=True),
    )
    assert result.status is Status.NEEDS_CONFIRM
    assert result.pending is not None
    assert result.pending.risk.value == "R2"
    assert "Update file.txt" in result.detail
    assert "-m" in result.evidence
    assert "Update file.txt" in result.evidence
    # No mutating commit yet.
    assert not any("commit" in a and "-m" in a and "confirmed" for a in [])
    assert not any(a[3:] == ("commit", "-m", "Update file.txt") for a in calls)


def test_commit_confirmed_runs(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()

    class Done:
        def __init__(self, code=0, stdout="", stderr=""):
            self.returncode = code
            self.stdout = stdout
            self.stderr = stderr
            self.timed_out = False
            self.cancelled = False

    def run_fn(cmd):
        argv = cmd.argv
        if "status" in argv and "--porcelain" in argv:
            return Done(0, " M file.txt\n")
        return Done(0)

    verbs = _verbs(run_fn=run_fn, which=lambda _b: "/usr/bin/git")
    result = verbs["vcs.commit"].handler(
        _intent(
            "vcs.commit",
            {"message": "Hello world"},
            modifiers=frozenset({"confirmed"}),
        ),
        _context(root, dirty=True),
    )
    assert result.status is Status.OK
    assert "Hello world" in result.detail


def test_push_force_needs_confirm_lease_only(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()

    class Done:
        returncode = 0
        stdout = ""
        stderr = ""
        timed_out = False
        cancelled = False

    verbs = _verbs(run_fn=lambda _c: Done(), which=lambda _b: "/usr/bin/git")
    result = verbs["vcs.push.force"].handler(
        _intent("vcs.push.force"),
        _context(root),
    )
    assert result.status is Status.NEEDS_CONFIRM
    assert result.pending is not None
    assert result.pending.risk.value == "R3"
    assert result.evidence[-1] == "--force-with-lease"
    assert "--force" not in result.evidence


def test_restore_needs_focus_and_shows_delta(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    doc = root / "a.txt"
    doc.write_text("x\n", encoding="utf-8")

    class Done:
        def __init__(self, code=0, stdout="", stderr=""):
            self.returncode = code
            self.stdout = stdout
            self.stderr = stderr
            self.timed_out = False
            self.cancelled = False

    def run_fn(cmd):
        if "--numstat" in cmd.argv:
            return Done(0, "3\t1\ta.txt\n")
        return Done(0)

    verbs = _verbs(run_fn=run_fn, which=lambda _b: "/usr/bin/git")
    refused = verbs["vcs.restore"].handler(
        _intent("vcs.restore"),
        _context(root),
    )
    assert refused.status is Status.REFUSED

    result = verbs["vcs.restore"].handler(
        _intent("vcs.restore"),
        _context(root, document=doc),
    )
    assert result.status is Status.NEEDS_CONFIRM
    assert "+3/-1" in result.detail
    assert "a.txt" in result.detail


def test_missing_git_binary_unsupported(tmp_path: Path) -> None:
    verbs = _verbs(which=lambda _b: None)
    result = verbs["vcs.status"].handler(
        _intent("vcs.status"),
        _context(tmp_path),
    )
    assert result.status is Status.UNSUPPORTED
    assert "git isn't installed" in result.summary
