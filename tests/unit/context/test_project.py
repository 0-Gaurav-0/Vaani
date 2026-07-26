"""Project profile detection across fixture repo shapes (T3.3 / §12.8)."""
from __future__ import annotations

from pathlib import Path

import pytest

from vaani.context import build_context
from vaani.context.focus import NullFocusProbe
from vaani.context.project import (
    CHECKED_FILES,
    ProfileMiss,
    clear_project_cache,
    detect_project,
    require_command,
)
from vaani.intent.schema import ProjectProfile
from vaani.platform.protocol import PlatformId

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "repos"


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    clear_project_cache()
    yield
    clear_project_cache()


def _repo(name: str) -> Path:
    path = FIXTURES / name
    assert path.is_dir(), f"missing fixture repo: {path}"
    return path


def test_npm_lockfile_and_scripts() -> None:
    profile = detect_project(_repo("npm"))
    assert isinstance(profile, ProjectProfile)
    assert profile.manager == "npm"
    assert profile.test == ("npm", "test")
    assert profile.build == ("npm", "run", "build")
    assert profile.dev == ("npm", "run", "dev")
    assert profile.lint == ("npm", "run", "lint")
    assert profile.typecheck == ("tsc", "--noEmit")


def test_pnpm_from_lockfile() -> None:
    profile = detect_project(_repo("pnpm"))
    assert isinstance(profile, ProjectProfile)
    assert profile.manager == "pnpm"
    assert profile.test == ("pnpm", "test")
    assert profile.dev == ("pnpm", "run", "dev")
    assert profile.typecheck == ("pnpm", "run", "typecheck")


def test_yarn_from_lockfile_uses_start_as_dev() -> None:
    profile = detect_project(_repo("yarn"))
    assert isinstance(profile, ProjectProfile)
    assert profile.manager == "yarn"
    assert profile.test == ("yarn", "test")
    assert profile.build == ("yarn", "build")
    assert profile.dev == ("yarn", "start")


def test_uv_python_never_guesses_npm() -> None:
    profile = detect_project(_repo("uv"))
    assert isinstance(profile, ProjectProfile)
    assert profile.manager == "uv"
    assert profile.test == ("pytest",)
    assert profile.test is not None
    assert profile.test[0] != "npm"
    assert profile.env_files == (profile.root / ".env",)


def test_poetry_lockfile() -> None:
    profile = detect_project(_repo("poetry"))
    assert isinstance(profile, ProjectProfile)
    assert profile.manager == "poetry"
    assert profile.test == ("pytest",)


def test_hatch_pyproject() -> None:
    profile = detect_project(_repo("hatch"))
    assert isinstance(profile, ProjectProfile)
    assert profile.manager == "hatch"
    assert profile.test == ("hatch", "test")


def test_makefile_targets() -> None:
    profile = detect_project(_repo("makefile"))
    assert isinstance(profile, ProjectProfile)
    assert profile.manager is None
    assert profile.test == ("make", "test")
    assert profile.build == ("make", "build")
    assert profile.lint == ("make", "lint")


def test_mixed_js_python_monorepo_prefers_js_lock_and_scripts() -> None:
    """pnpm-lock wins manager; package.json scripts.test wins over pyproject."""
    profile = detect_project(_repo("mixed-js-py"))
    assert isinstance(profile, ProjectProfile)
    assert profile.manager == "pnpm"
    assert profile.test == ("pnpm", "test")
    assert profile.dev == ("pnpm", "run", "dev")
    assert profile.compose_file == profile.root / "docker-compose.yml"
    assert profile.env_files == (profile.root / ".env.local",)


def test_none_refuses_and_names_checked_files() -> None:
    miss = detect_project(_repo("none"))
    assert isinstance(miss, ProfileMiss)
    message = miss.refusal_message()
    assert "No project profile detected" in message
    for name in CHECKED_FILES:
        assert name in message, f"refusal must name checked file {name!r}"
    assert "package.json" in message
    assert "pyproject.toml" in message
    assert "Makefile" in message
    assert "vaani.toml" in message


def test_vaani_toml_overrides_detected_commands() -> None:
    profile = detect_project(_repo("vaani-override"))
    assert isinstance(profile, ProjectProfile)
    assert profile.manager == "uv"
    assert profile.test == ("uv", "run", "pytest", "-k", "unit")
    assert profile.build == ("make", "dist")
    assert profile.dev == ("uvicorn", "app:main", "--reload")
    assert profile.typecheck == ("ty", "check")
    assert profile.lint == ("ruff", "check", "src")
    assert profile.compose_file == profile.root / "compose.yml"
    assert profile.env_files == (
        profile.root / ".env",
        profile.root / ".env.local",
    )


def test_python_only_require_test_does_not_invent_npm(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='solo'\nversion='0'\n",
        encoding="utf-8",
    )
    # No pytest config, no tests/ → command miss lists checked files.
    miss = require_command(tmp_path, "test")
    assert isinstance(miss, ProfileMiss)
    message = miss.refusal_message()
    assert "package.json" in message
    assert "pyproject.toml" in message
    profile = detect_project(tmp_path)
    assert isinstance(profile, ProjectProfile)
    assert profile.manager == "pip"
    assert profile.test is None


def test_cache_invalidates_on_mtime_change(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"scripts":{"test":"jest"}}',
        encoding="utf-8",
    )
    (tmp_path / "package-lock.json").write_text("{}", encoding="utf-8")
    first = detect_project(tmp_path)
    assert isinstance(first, ProjectProfile)
    assert first.manager == "npm"

    # Add a higher-priority lockfile; mtime fingerprint must bust the cache.
    (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: '9'\n", encoding="utf-8")
    second = detect_project(tmp_path)
    assert isinstance(second, ProjectProfile)
    assert second.manager == "pnpm"


def test_build_context_wires_project_when_workspace_known(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    (root / "uv.lock").write_text("", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        "[project]\nname='x'\nversion='0'\n\n[tool.pytest.ini_options]\n",
        encoding="utf-8",
    )
    ctx = build_context(
        PlatformId.LINUX,
        focus_probe=NullFocusProbe(),
        environ={"VAANI_ASSISTANT_CWD": str(root)},
        home=home,
        last_used=None,
        remember=False,
        include_repo=False,
    )
    assert ctx.workspace == root.resolve()
    assert ctx.project is not None
    assert ctx.project.manager == "uv"
    assert ctx.project.test == ("pytest",)


def test_build_context_project_none_on_empty_workspace(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    ctx = build_context(
        PlatformId.MACOS,
        focus_probe=NullFocusProbe(),
        environ={"VAANI_ASSISTANT_CWD": str(empty)},
        home=home,
        last_used=None,
        remember=False,
        include_repo=False,
    )
    assert ctx.workspace == empty.resolve()
    assert ctx.project is None
