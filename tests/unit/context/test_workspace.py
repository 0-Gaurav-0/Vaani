"""Workspace precedence table (spec §5.2 / T3.2)."""
from __future__ import annotations

from pathlib import Path

import pytest

from vaani.context.workspace import (
    SOURCE_CONFIG,
    SOURCE_FOCUSED_EDITOR,
    SOURCE_FOCUSED_TERMINAL,
    SOURCE_HOME,
    SOURCE_LAST_USED,
    clear_last_used,
    resolve_workspace,
    set_last_used,
)


@pytest.fixture(autouse=True)
def _clean_last_used() -> None:
    clear_last_used()
    yield
    clear_last_used()


def test_precedence_focused_editor_wins(tmp_path: Path) -> None:
    editor = tmp_path / "editor"
    term = tmp_path / "term"
    editor.mkdir()
    term.mkdir()
    env = {"VAANI_ASSISTANT_CWD": str(tmp_path / "env")}
    (tmp_path / "env").mkdir()
    set_last_used(tmp_path / "last")
    (tmp_path / "last").mkdir()
    home = tmp_path / "home"
    home.mkdir()

    path, source = resolve_workspace(
        editor_project=editor,
        terminal_cwd=term,
        environ=env,
        last_used=tmp_path / "last",
        home=home,
    )
    assert path == editor.resolve()
    assert source == SOURCE_FOCUSED_EDITOR


def test_precedence_focused_terminal(tmp_path: Path) -> None:
    term = tmp_path / "term"
    term.mkdir()
    env = {"VAANI_ASSISTANT_CWD": str(tmp_path / "env")}
    (tmp_path / "env").mkdir()
    home = tmp_path / "home"
    home.mkdir()

    path, source = resolve_workspace(
        editor_project=None,
        terminal_cwd=term,
        environ=env,
        last_used=None,
        home=home,
    )
    assert path == term.resolve()
    assert source == SOURCE_FOCUSED_TERMINAL


def test_precedence_config_env(tmp_path: Path) -> None:
    env_dir = tmp_path / "env"
    env_dir.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    path, source = resolve_workspace(
        editor_project=None,
        terminal_cwd=None,
        environ={"VAANI_ASSISTANT_CWD": str(env_dir)},
        last_used=None,
        home=home,
    )
    assert path == env_dir.resolve()
    assert source == SOURCE_CONFIG


def test_precedence_last_used(tmp_path: Path) -> None:
    last = tmp_path / "last"
    last.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    path, source = resolve_workspace(
        editor_project=None,
        terminal_cwd=None,
        environ={},
        last_used=last,
        home=home,
    )
    assert path == last.resolve()
    assert source == SOURCE_LAST_USED


def test_precedence_home(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    path, source = resolve_workspace(
        editor_project=None,
        terminal_cwd=None,
        environ={},
        last_used=None,
        home=home,
    )
    assert path == home.resolve()
    assert source == SOURCE_HOME


def test_missing_paths_fall_through(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    path, source = resolve_workspace(
        editor_project=tmp_path / "missing-editor",
        terminal_cwd=tmp_path / "missing-term",
        environ={"VAANI_ASSISTANT_CWD": str(tmp_path / "missing-env")},
        last_used=tmp_path / "missing-last",
        home=home,
    )
    assert path == home.resolve()
    assert source == SOURCE_HOME


@pytest.mark.parametrize(
    ("kwargs", "expected_source"),
    [
        ({"editor_project": "ed"}, SOURCE_FOCUSED_EDITOR),
        ({"terminal_cwd": "term"}, SOURCE_FOCUSED_TERMINAL),
        ({"environ_key": "env"}, SOURCE_CONFIG),
        ({"last": "last"}, SOURCE_LAST_USED),
        ({}, SOURCE_HOME),
    ],
)
def test_precedence_table(
    tmp_path: Path, kwargs: dict[str, str], expected_source: str
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    editor = terminal = env_dir = last = None
    environ: dict[str, str] = {}
    if "editor_project" in kwargs:
        editor = tmp_path / kwargs["editor_project"]
        editor.mkdir()
    if "terminal_cwd" in kwargs:
        terminal = tmp_path / kwargs["terminal_cwd"]
        terminal.mkdir()
    if "environ_key" in kwargs:
        env_dir = tmp_path / kwargs["environ_key"]
        env_dir.mkdir()
        environ = {"VAANI_ASSISTANT_CWD": str(env_dir)}
    if "last" in kwargs:
        last = tmp_path / kwargs["last"]
        last.mkdir()

    _path, source = resolve_workspace(
        editor_project=editor,
        terminal_cwd=terminal,
        environ=environ,
        last_used=last,
        home=home,
    )
    assert source == expected_source


# Manual checklist (wrong-workspace failure mode — not automated):
# 1. Focus an editor in repo A, run a workspace-scoped verb → workspace_source=focused-editor, path=A.
# 2. Focus a terminal in repo B cwd, no editor focus → focused-terminal / B.
# 3. No focus, VAANI_ASSISTANT_CWD=C → config / C.
# 4. Confirm Result JSON always includes workspace_source (never silent wrong-repo).
