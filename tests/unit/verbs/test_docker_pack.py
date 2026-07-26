"""Unit tests for T4.4 container / docker pack."""
from __future__ import annotations

from pathlib import Path

from vaani.context.project import clear_project_cache
from vaani.exec.runner import Completed
from vaani.intent.grammar import match
from vaani.intent.schema import Context, Intent, ProjectProfile, Status, Support
from vaani.platform.protocol import PlatformId
from vaani.verbs.packs.docker import (
    DOCKER_VERB_NAMES,
    build_docker_verbs,
    docker_patterns,
    list_compose_services,
    resolve_compose_service,
)
from vaani.verbs.packs.registry import PackRegistry
from vaani.verbs.registry import Registry


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
    tmp_path: Path | None = None,
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


def _compose_project(tmp_path: Path) -> ProjectProfile:
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        """
services:
  api:
    image: api:latest
  web:
    image: web:latest
  worker:
    image: worker:latest
volumes:
  data:
""".lstrip(),
        encoding="utf-8",
    )
    return ProjectProfile(root=tmp_path, manager=None, compose_file=compose)


def _verbs(**kwargs):
    return {v.name: v for v in build_docker_verbs(**kwargs)}


def test_docker_verb_names() -> None:
    assert set(_verbs(which=lambda b: f"/bin/{b}")) == DOCKER_VERB_NAMES


def test_list_compose_services(tmp_path: Path) -> None:
    profile = _compose_project(tmp_path)
    assert list_compose_services(profile.compose_file) == ("api", "web", "worker")


def test_resolve_compose_service_exact_and_fuzzy() -> None:
    services = ("api", "web", "worker")
    assert resolve_compose_service("api", services) == "api"
    assert resolve_compose_service("api container", services) == "api"
    assert resolve_compose_service("wor", services) == "worker"


def test_resolve_compose_service_ambiguous() -> None:
    services = ("api", "api-gateway", "web")
    resolved = resolve_compose_service("api", services)
    # exact wins over prefix siblings
    assert resolved == "api"
    resolved = resolve_compose_service("ap", services)
    assert isinstance(resolved, tuple)
    assert set(resolved) == {"api", "api-gateway"}


def test_logs_resolves_service_against_compose(tmp_path: Path) -> None:
    clear_project_cache()
    profile = _compose_project(tmp_path)
    calls: list[tuple[str, ...]] = []

    def run_fn(cmd, **_kw):
        calls.append(tuple(cmd.argv))
        return Completed(argv=tuple(cmd.argv), returncode=0, stdout="ok", stderr="")

    verbs = _verbs(run_fn=run_fn, which=lambda b: f"/bin/{b}")
    result = verbs["container.logs"].handler(
        _intent("container.logs", {"service": "api"}),
        _context(tmp_path, project=profile),
    )
    assert result.status is Status.OK
    assert calls[0][:4] == ("docker", "compose", "-f", "docker-compose.yml")
    assert calls[0][-1] == "api"


def test_logs_mishear_refuses_with_options(tmp_path: Path) -> None:
    clear_project_cache()
    profile = _compose_project(tmp_path)
    # Add a second service that shares a prefix with a mishear.
    compose = profile.compose_file
    assert compose is not None
    compose.write_text(
        """
services:
  api:
    image: api:latest
  api-gateway:
    image: gw:latest
  web:
    image: web:latest
""".lstrip(),
        encoding="utf-8",
    )
    verbs = _verbs(which=lambda b: f"/bin/{b}")
    result = verbs["container.logs"].handler(
        _intent("container.logs", {"service": "ap"}),
        _context(tmp_path, project=profile),
    )
    assert result.status is Status.REFUSED
    assert "Ambiguous" in result.summary or "matches" in result.detail.casefold()
    assert "api" in result.detail
    assert "api-gateway" in result.detail


def test_logs_disambiguate_hook_selects(tmp_path: Path) -> None:
    clear_project_cache()
    profile = _compose_project(tmp_path)
    compose = profile.compose_file
    assert compose is not None
    compose.write_text(
        """
services:
  api:
    image: api:latest
  api-gateway:
    image: gw:latest
""".lstrip(),
        encoding="utf-8",
    )
    calls: list[tuple[str, ...]] = []

    def run_fn(cmd, **_kw):
        calls.append(tuple(cmd.argv))
        return Completed(argv=tuple(cmd.argv), returncode=0, stdout="", stderr="")

    verbs = _verbs(
        run_fn=run_fn,
        which=lambda b: f"/bin/{b}",
        disambiguate=lambda options, _prompt: "api-gateway",
    )
    result = verbs["container.logs"].handler(
        _intent("container.logs", {"service": "ap"}),
        _context(tmp_path, project=profile),
    )
    assert result.status is Status.OK
    assert calls[0][-1] == "api-gateway"


def test_stop_all_confirm_lists_names(tmp_path: Path) -> None:
    verbs = _verbs(
        which=lambda b: f"/bin/{b}",
        list_containers=lambda: ("api", "web"),
    )
    result = verbs["container.stop_all"].handler(
        _intent("container.stop_all"),
        _context(tmp_path),
    )
    assert result.status is Status.NEEDS_CONFIRM
    assert result.pending is not None
    assert result.pending.risk.value == "R3"
    assert "api" in result.detail and "web" in result.detail
    assert result.evidence == ("docker", "stop", "api", "web")


def test_stop_all_dry_run(tmp_path: Path) -> None:
    verbs = _verbs(
        which=lambda b: f"/bin/{b}",
        list_containers=lambda: ("db",),
    )
    result = verbs["container.stop_all"].handler(
        _intent("container.stop_all", modifiers=frozenset({"dry_run"})),
        _context(tmp_path),
    )
    assert result.status is Status.DRY_RUN
    assert result.evidence == ("docker", "stop", "db")


def test_missing_docker_degraded_unsupported(tmp_path: Path) -> None:
    verbs = _verbs(which=lambda _b: None)
    result = verbs["container.list"].handler(
        _intent("container.list"),
        _context(tmp_path),
    )
    assert result.status is Status.UNSUPPORTED
    assert "docker isn't installed" in result.summary


def test_pack_disabled_matrix_unsupported(tmp_path: Path) -> None:
    packs = PackRegistry(tmp_path / "packs.json", which=lambda b: f"/bin/{b}")
    assert packs.is_enabled("docker") is False
    registry = Registry()
    for verb in build_docker_verbs(which=lambda b: f"/bin/{b}"):
        registry.register(verb)
    packs.apply(registry)
    support, note = registry.matrix()["container.list"]["linux"]
    assert support is Support.UNSUPPORTED
    assert note == "pack disabled"


def test_docker_patterns() -> None:
    patterns = docker_patterns()
    assert match("start docker", patterns)[0] == "container.engine.start"
    assert match("list running containers", patterns)[0] == "container.list"
    assert match("stop all containers", patterns)[0] == "container.stop_all"
    assert match("rebuild the compose stack", patterns)[0] == "container.compose.rebuild"
    hit = match("tail logs for the api container", patterns)
    assert hit is not None
    assert hit[0] == "container.logs"
    assert hit[1].get("service") in {"api", "api container"}
