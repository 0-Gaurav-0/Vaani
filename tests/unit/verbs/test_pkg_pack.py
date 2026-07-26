"""Unit tests for T4.4 package-manager pack."""
from __future__ import annotations

from pathlib import Path

from vaani.context.project import clear_project_cache
from vaani.exec.runner import Completed
from vaani.intent.grammar import match
from vaani.intent.schema import Context, Intent, ProjectProfile, Status
from vaani.platform.protocol import PlatformId
from vaani.verbs.packs.pkg import (
    PKG_VERB_NAMES,
    build_pkg_verbs,
    path_inside_workspace,
    pkg_patterns,
)
from vaani.verbs.packs.registry import PackRegistry
from vaani.verbs.registry import Registry
from vaani.intent.schema import Support


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
    tmp_path: Path,
    *,
    project: ProjectProfile | None = None,
    platform: PlatformId = PlatformId.MACOS,
) -> Context:
    return Context(
        platform=platform,
        workspace=tmp_path,
        workspace_source="test",
        repo=None,
        project=project,
        focus=None,
        screen=None,
        session=None,
    )


def _npm_project(tmp_path: Path) -> ProjectProfile:
    (tmp_path / "package-lock.json").write_text("{}", encoding="utf-8")
    (tmp_path / "package.json").write_text(
        '{"scripts": {"lint": "eslint .", "test": "jest"}}',
        encoding="utf-8",
    )
    return ProjectProfile(root=tmp_path, manager="npm")


def _verbs(*, run_fn=None, which=None, rmtree=None):
    return {
        v.name: v
        for v in build_pkg_verbs(run_fn=run_fn, which=which, rmtree=rmtree)
    }


def test_pkg_verb_names() -> None:
    assert set(_verbs()) == PKG_VERB_NAMES


def test_manager_from_lockfile_not_speech(tmp_path: Path) -> None:
    clear_project_cache()
    calls: list[tuple[str, ...]] = []

    def run_fn(cmd, **_kw):
        calls.append(tuple(cmd.argv))
        return Completed(argv=tuple(cmd.argv), returncode=0, stdout="", stderr="")

    profile = _npm_project(tmp_path)
    verbs = _verbs(run_fn=run_fn, which=lambda b: f"/bin/{b}")
    result = verbs["pkg.add"].handler(
        _intent(
            "pkg.add",
            {"package": "lodash"},
            modifiers=frozenset({"confirmed"}),
        ),
        _context(tmp_path, project=profile),
    )
    assert result.status is Status.OK
    assert calls == [("npm", "install", "lodash")]


def test_pkg_add_needs_confirm_shows_heard_name(tmp_path: Path) -> None:
    profile = _npm_project(tmp_path)
    verbs = _verbs(which=lambda b: f"/bin/{b}")
    result = verbs["pkg.add"].handler(
        _intent("pkg.add", {"package": "lodahs"}),  # mis-hear
        _context(tmp_path, project=profile),
    )
    assert result.status is Status.NEEDS_CONFIRM
    assert result.pending is not None
    assert "lodahs" in result.detail
    assert result.evidence == ("npm", "install", "lodahs")


def test_pkg_add_dry_run(tmp_path: Path) -> None:
    profile = _npm_project(tmp_path)
    verbs = _verbs(which=lambda b: f"/bin/{b}")
    result = verbs["pkg.add"].handler(
        _intent(
            "pkg.add",
            {"package": "requests"},
            modifiers=frozenset({"dry_run"}),
        ),
        _context(tmp_path, project=profile),
    )
    assert result.status is Status.DRY_RUN
    assert result.evidence[0] == "npm"


def test_reinstall_refuses_dotdot_escape(tmp_path: Path) -> None:
    clear_project_cache()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    profile = _npm_project(workspace)
    removed: list[Path] = []

    verbs = _verbs(
        which=lambda b: f"/bin/{b}",
        rmtree=lambda p: removed.append(p),
    )
    result = verbs["pkg.reinstall"].handler(
        _intent(
            "pkg.reinstall",
            {"path": "../outside"},
            modifiers=frozenset({"confirmed"}),
        ),
        _context(workspace, project=profile),
    )
    assert result.status is Status.REFUSED
    assert "escape" in result.detail.casefold() or "escapes" in result.summary.casefold()
    assert removed == []


def test_reinstall_refuses_symlink_escape(tmp_path: Path) -> None:
    clear_project_cache()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    outside = tmp_path / "evil"
    outside.mkdir()
    link = workspace / "node_modules"
    link.symlink_to(outside)
    profile = _npm_project(workspace)
    removed: list[Path] = []

    verbs = _verbs(
        which=lambda b: f"/bin/{b}",
        rmtree=lambda p: removed.append(p),
    )
    result = verbs["pkg.reinstall"].handler(
        _intent(
            "pkg.reinstall",
            {"path": "node_modules"},
            modifiers=frozenset({"confirmed"}),
        ),
        _context(workspace, project=profile),
    )
    assert result.status is Status.REFUSED
    assert removed == []
    # Helper agrees the link escapes.
    assert path_inside_workspace(workspace, "node_modules") is None


def test_reinstall_deletes_inside_workspace(tmp_path: Path) -> None:
    clear_project_cache()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    node_modules = workspace / "node_modules"
    node_modules.mkdir()
    (node_modules / "pkg").mkdir()
    profile = _npm_project(workspace)
    removed: list[Path] = []
    calls: list[tuple[str, ...]] = []

    def run_fn(cmd, **_kw):
        calls.append(tuple(cmd.argv))
        return Completed(argv=tuple(cmd.argv), returncode=0, stdout="", stderr="")

    verbs = _verbs(
        run_fn=run_fn,
        which=lambda b: f"/bin/{b}",
        rmtree=lambda p: removed.append(p),
    )
    result = verbs["pkg.reinstall"].handler(
        _intent(
            "pkg.reinstall",
            {"path": "node_modules"},
            modifiers=frozenset({"confirmed"}),
        ),
        _context(workspace, project=profile),
    )
    assert result.status is Status.OK
    assert removed == [node_modules.resolve()]
    assert calls == [("npm", "install")]


def test_script_run_unknown_lists_scripts(tmp_path: Path) -> None:
    profile = _npm_project(tmp_path)
    verbs = _verbs(which=lambda b: f"/bin/{b}")
    result = verbs["pkg.script.run"].handler(
        _intent("pkg.script.run", {"script": "nope"}),
        _context(tmp_path, project=profile),
    )
    assert result.status is Status.REFUSED
    assert "lint" in result.detail
    assert "test" in result.detail


def test_missing_manager_binary_unsupported(tmp_path: Path) -> None:
    profile = _npm_project(tmp_path)
    verbs = _verbs(which=lambda _b: None)
    result = verbs["pkg.lock"].handler(
        _intent("pkg.lock"),
        _context(tmp_path, project=profile),
    )
    assert result.status is Status.UNSUPPORTED
    assert "npm isn't installed" in result.summary


def test_pack_disabled_matrix_unsupported(tmp_path: Path) -> None:
    packs = PackRegistry(tmp_path / "packs.json", which=lambda b: f"/bin/{b}")
    assert packs.is_enabled("pkg") is False
    registry = Registry()
    for verb in build_pkg_verbs(which=lambda b: f"/bin/{b}"):
        registry.register(verb)
    packs.apply(registry)
    support, note = registry.matrix()["pkg.add"]["linux"]
    assert support is Support.UNSUPPORTED
    assert note == "pack disabled"
    assert registry.enabled(PlatformId.LINUX) == ()


def test_pkg_patterns_corpus_phrases() -> None:
    patterns = pkg_patterns()
    assert match("install lodash", patterns)[0] == "pkg.add"
    assert match("add requests to the project", patterns)[0] == "pkg.add"
    assert match("update the lockfile", patterns)[0] == "pkg.lock"
    assert match("run npm run lint", patterns)[0] == "pkg.script.run"
    assert match("remove node_modules and reinstall", patterns)[0] == "pkg.reinstall"
