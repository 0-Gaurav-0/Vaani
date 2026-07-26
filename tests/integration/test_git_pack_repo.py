"""Temp-repo integration for T4.2 git pack (create / collision / dirty-tree)."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from vaani.exec.runner import run as exec_run
from vaani.intent.schema import Context, Intent, RepoInfo, Status
from vaani.platform.protocol import PlatformId
from vaani.verbs.packs.git import build_git_verbs

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git binary required"
)


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", os.fspath(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _init_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "vaani@example.com")
    _git(root, "config", "user.name", "Vaani Test")
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-m", "init")
    return root


def _intent(verb: str, slots: dict | None = None, *, confirmed: bool = False) -> Intent:
    mods = frozenset({"confirmed"}) if confirmed else frozenset()
    return Intent(
        verb=verb,
        slots=slots or {},
        rung=4,
        confidence=1.0,
        source="test",
        mode="act",
        utterance=verb,
        raw_utterance=verb,
        modifiers=mods,
        brain=None,
    )


def _context(root: Path, *, dirty: bool = False) -> Context:
    return Context(
        platform=PlatformId.LINUX,
        workspace=root,
        workspace_source="test",
        repo=RepoInfo(root=root, branch="main", dirty=dirty),
        project=None,
        focus=None,
        screen=None,
        session=None,
    )


def test_branch_create_slug_and_switch(tmp_path: Path) -> None:
    root = _init_repo(tmp_path)
    verbs = {v.name: v for v in build_git_verbs(run_fn=exec_run)}
    # First call materializes slug and asks confirm.
    preview = verbs["vcs.branch.create"].handler(
        _intent("vcs.branch.create", {"name": "assistant use cases"}),
        _context(root),
    )
    assert preview.status is Status.NEEDS_CONFIRM
    assert "assistant-use-cases" in preview.summary

    result = verbs["vcs.branch.create"].handler(
        _intent(
            "vcs.branch.create",
            {"name": "assistant use cases", "slug": "assistant-use-cases"},
            confirmed=True,
        ),
        _context(root),
    )
    assert result.status is Status.OK
    head = subprocess.check_output(
        ["git", "-C", os.fspath(root), "rev-parse", "--abbrev-ref", "HEAD"],
        text=True,
    ).strip()
    assert head == "assistant-use-cases"


def test_branch_create_collision(tmp_path: Path) -> None:
    root = _init_repo(tmp_path)
    _git(root, "branch", "linux-fix")
    verbs = {v.name: v for v in build_git_verbs(run_fn=exec_run)}
    result = verbs["vcs.branch.create"].handler(
        _intent("vcs.branch.create", {"name": "linux fix"}, confirmed=True),
        _context(root),
    )
    assert result.status is Status.REFUSED
    assert "already exists" in result.summary.casefold()


def test_branch_create_check_ref_format_rejection(tmp_path: Path) -> None:
    root = _init_repo(tmp_path)
    verbs = {v.name: v for v in build_git_verbs(run_fn=exec_run)}
    result = verbs["vcs.branch.create"].handler(
        _intent(
            "vcs.branch.create",
            {"name": "bad..name", "slug": "bad..name"},
            confirmed=True,
        ),
        _context(root),
    )
    assert result.status is Status.FAILED
    assert "invalid" in result.summary.casefold()


def test_checkout_dirty_tree_needs_confirm(tmp_path: Path) -> None:
    root = _init_repo(tmp_path)
    _git(root, "branch", "other")
    (root / "README.md").write_text("dirty\n", encoding="utf-8")
    verbs = {v.name: v for v in build_git_verbs(run_fn=exec_run)}
    result = verbs["vcs.checkout"].handler(
        _intent("vcs.checkout", {"ref": "other"}),
        _context(root, dirty=True),
    )
    assert result.status is Status.NEEDS_CONFIRM
    assert result.pending is not None
    assert result.pending.risk.value == "R2"
    assert "dirty" in result.detail.casefold()


def test_feature_slash_port_killer_slug(tmp_path: Path) -> None:
    root = _init_repo(tmp_path)
    verbs = {v.name: v for v in build_git_verbs(run_fn=exec_run)}
    result = verbs["vcs.branch.create"].handler(
        _intent(
            "vcs.branch.create",
            {
                "name": "feature slash port killer",
                "slug": "feature/port-killer",
            },
            confirmed=True,
        ),
        _context(root),
    )
    assert result.status is Status.OK
    head = subprocess.check_output(
        ["git", "-C", os.fspath(root), "branch", "--list", "feature/port-killer"],
        text=True,
    ).strip()
    assert "feature/port-killer" in head
