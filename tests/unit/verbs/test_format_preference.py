"""T5.3 — prefer verifiable project.format over editor.format macros."""
from __future__ import annotations

from pathlib import Path

from vaani.apps import launch_app, resolve_app
from vaani.exec.input import FakeInputSynth
from vaani.intent.grammar import match
from vaani.intent.router import Router, prefer_verifiable_format
from vaani.intent.schema import Context, Intent, ProjectProfile, Status
from vaani.platform.protocol import PlatformId
from vaani.sites import resolve_site
from vaani.verbs.packs.computer_use import computer_use_patterns, register_computer_use_pack
from vaani.verbs.packs.core import build_core_registry
from vaani.verbs.packs.project import project_patterns
from vaani.verbs.packs.registry import PackRegistry
from vaani.verbs.registry import Registry


def _intent(verb: str) -> Intent:
    return Intent(
        verb=verb,
        slots={},
        rung=4 if verb == "project.format" else 7,
        confidence=1.0,
        source="grammar",
        mode="act",
        utterance="format this file",
        raw_utterance="format this file",
        modifiers=frozenset(),
        brain=None,
    )


def _context(*, profile: ProjectProfile | None) -> Context:
    return Context(
        platform=PlatformId.LINUX,
        workspace=Path("/tmp/proj") if profile is None else profile.root,
        workspace_source="test",
        repo=None,
        project=profile,
        focus=None,
        screen=None,
        session=None,
    )


def test_grammar_prefers_project_format_over_editor_format() -> None:
    patterns = project_patterns() + computer_use_patterns()
    hit = match("format this file", patterns)
    assert hit is not None
    assert hit[0] == "project.format"
    assert hit[2] >= 50  # early-router priority band


def test_prefer_verifiable_when_profile_has_formatter(tmp_path: Path) -> None:
    registry = Registry()
    register_computer_use_pack(registry, get_input=lambda: FakeInputSynth())
    # project.format comes from core/project registration
    from vaani.verbs.packs.project import register_project_pack

    register_project_pack(registry, run_fn=lambda *_a, **_k: None)
    packs = PackRegistry(tmp_path / "packs.json", which=lambda _b: None)
    packs.set_enabled("computer-use", True)
    packs.apply(registry)

    profile = ProjectProfile(
        root=tmp_path,
        format=("ruff", "format", "."),
    )
    ctx = _context(profile=profile)
    preferred = prefer_verifiable_format(
        _intent("editor.format"),
        ctx,
        registry,
        platform=PlatformId.LINUX,
    )
    assert preferred.verb == "project.format"
    assert preferred.rung == 4


def test_prefer_editor_macro_when_no_formatter(tmp_path: Path) -> None:
    registry = Registry()
    register_computer_use_pack(registry, get_input=lambda: FakeInputSynth())
    from vaani.verbs.packs.project import register_project_pack

    register_project_pack(registry, run_fn=lambda *_a, **_k: None)
    packs = PackRegistry(tmp_path / "packs.json", which=lambda _b: None)
    packs.set_enabled("computer-use", True)
    packs.apply(registry)

    profile = ProjectProfile(root=tmp_path)  # no format / lint
    ctx = _context(profile=profile)
    preferred = prefer_verifiable_format(
        _intent("project.format"),
        ctx,
        registry,
        platform=PlatformId.LINUX,
    )
    assert preferred.verb == "editor.format"
    assert preferred.rung == 7


def test_router_routes_format_to_project_format(tmp_path: Path) -> None:
    synth = FakeInputSynth()
    registry, patterns = build_core_registry(
        resolve_app_fn=resolve_app,
        launch_app_fn=launch_app,
        resolve_site_fn=resolve_site,
        open_browser_fn=lambda **_k: "ok",
        run_command=lambda *_a, **_k: None,
    )
    patterns = patterns + register_computer_use_pack(
        registry, get_input=lambda: synth
    )
    # Leave computer-use disabled so only project.format is enabled.
    PackRegistry(tmp_path / "packs.json").apply(registry)
    router = Router(registry, patterns, resolve_app=resolve_app, resolve_site=resolve_site)
    intent = router.route("format this file", platform=PlatformId.LINUX)
    assert intent is not None
    assert intent.verb == "project.format"
    assert intent.rung == 4


def test_project_format_runs_argv(tmp_path: Path) -> None:
    calls: list[tuple[str, ...]] = []

    class Done:
        returncode = 0
        stdout = "formatted"
        stderr = ""
        timed_out = False
        cancelled = False

    def run_fn(cmd, cancel=None):
        _ = cancel
        calls.append(tuple(cmd.argv))
        return Done()

    from vaani.verbs.packs.project import build_project_verbs

    verbs = {v.name: v for v in build_project_verbs(run_fn=run_fn)}
    profile = ProjectProfile(root=tmp_path, format=("ruff", "format", "."))
    ctx = _context(profile=profile)
    result = verbs["project.format"].handler(_intent("project.format"), ctx)
    assert result.status is Status.OK
    assert result.rung == 4
    assert calls == [("ruff", "format", ".")]
