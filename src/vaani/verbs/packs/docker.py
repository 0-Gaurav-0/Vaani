"""Container / docker pack (T4.4): engine, list, stop, compose, logs.

Service names resolve against the compose file so mishears become disambiguation
(or ``REFUSED`` with options when ``DisambiguateEngine`` is not available).
"""
from __future__ import annotations

import re
import shutil
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from vaani.context.project import ProfileMiss, detect_project
from vaani.intent.grammar import Pattern, SlotRule
from vaani.intent.schema import (
    Context,
    Intent,
    PendingAction,
    ProjectProfile,
    Result,
    RiskClass,
    SlotSpec,
    Status,
    Support,
    Verb,
)
from vaani.platform.protocol import PlatformId
from vaani.verbs.packs.registry import missing_binary_reason
from vaani.verbs.registry import Registry

PACK_NAME = "docker"

DOCKER_VERB_NAMES: frozenset[str] = frozenset(
    {
        "container.engine.start",
        "container.list",
        "container.stop",
        "container.stop_all",
        "container.compose.rebuild",
        "container.logs",
    }
)

_ALL_SUPPORT = {
    PlatformId.LINUX: Support.SUPPORTED,
    PlatformId.MACOS: Support.SUPPORTED,
    PlatformId.WINDOWS: Support.SUPPORTED,
}

_PENDING_TTL_S = 20.0
_ONESHOT_TIMEOUT_S = 600.0
_DOCKER_BIN = "docker"

RunFn = Callable[..., Any]
WhichFn = Callable[[str], str | None]
PlatformGetter = Callable[[], PlatformId]
# Optional T4.5 seam: ``(candidates, prompt) -> chosen | None``.
DisambiguateFn = Callable[[Sequence[str], str], str | None]


@dataclass(frozen=True)
class ArgvCommand:
    """Duck-typed argv carrier for injected runner (no L5 import)."""

    argv: tuple[str, ...]
    cwd: Path | None = None
    timeout: float = 20.0
    env_extra: Mapping[str, str] = field(default_factory=dict)


def docker_patterns() -> tuple[Pattern, ...]:
    """Grammar rows for CLI-DOCK-01..05 (+ container.stop)."""
    return (
        Pattern(
            verb="container.engine.start",
            any_of=(
                (
                    "start docker",
                    "start the docker daemon",
                    "start docker desktop",
                    "launch docker",
                    "launch docker desktop",
                ),
            ),
            exact=True,
            priority=56,
        ),
        Pattern(
            verb="container.list",
            any_of=(
                (
                    "list running containers",
                    "list containers",
                    "show running containers",
                    "show containers",
                ),
            ),
            exact=True,
            priority=56,
        ),
        Pattern(
            verb="container.stop_all",
            any_of=(
                (
                    "stop all containers",
                    "stop every container",
                    "kill all containers",
                ),
            ),
            exact=True,
            priority=58,
        ),
        Pattern(
            verb="container.compose.rebuild",
            any_of=(
                (
                    "rebuild the compose stack",
                    "rebuild compose stack",
                    "rebuild the docker compose stack",
                    "rebuild docker compose",
                ),
            ),
            exact=True,
            priority=58,
        ),
        Pattern(
            verb="container.logs",
            any_of=(("tail logs for the", "tail logs for", "show logs for the", "show logs for"),),
            slots=(
                SlotRule(
                    name="service",
                    regex=r"(?:tail|show)\s+logs\s+for\s+(?:the\s+)?(.+?)(?:\s+container)?$",
                ),
            ),
            priority=58,
        ),
        Pattern(
            verb="container.stop",
            any_of=(("stop the", "kill the", "stop", "kill"), ("container",)),
            slots=(
                SlotRule(
                    name="service",
                    regex=r"(?:stop|kill)\s+(?:the\s+)?(.+?)\s+container",
                ),
            ),
            exclude=("all containers", "every container"),
            priority=57,
        ),
    )


def list_compose_services(compose_file: Path) -> tuple[str, ...]:
    """Parse service names from a compose YAML without a YAML dependency."""
    try:
        text = compose_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ()
    services: list[str] = []
    in_services = False
    base_indent: int | None = None
    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        if re.match(r"^services\s*:", raw_line):
            in_services = True
            base_indent = None
            continue
        if not in_services:
            continue
        # A new top-level key ends the services block.
        if raw_line[0] not in {" ", "\t"}:
            break
        match = re.match(r"^([ \t]+)([A-Za-z0-9][A-Za-z0-9_.-]*)\s*:", raw_line)
        if match is None:
            continue
        indent = len(match.group(1).expandtabs(2))
        name = match.group(2)
        if base_indent is None:
            base_indent = indent
        if indent == base_indent:
            if not name.startswith(".") and name not in {"x-anchors", "x-common"}:
                services.append(name)
        elif indent < base_indent:
            break
    seen: set[str] = set()
    out: list[str] = []
    for name in services:
        if name not in seen:
            seen.add(name)
            out.append(name)
    return tuple(out)


def resolve_compose_service(
    heard: str,
    services: Sequence[str],
) -> str | tuple[str, ...] | None:
    """Resolve a spoken service name against compose services.

    Returns:
      - exact / unique fuzzy match as ``str``
      - multiple candidates as ``tuple[str, ...]``
      - ``None`` when nothing matches
    """
    needle = heard.strip().casefold()
    if not needle or not services:
        return None
    # Strip trailing "container" if still present.
    if needle.endswith(" container"):
        needle = needle[: -len(" container")].strip()
    exact = [s for s in services if s.casefold() == needle]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        return tuple(exact)
    # Prefix / contains matches for mishears.
    fuzzy = [
        s
        for s in services
        if needle in s.casefold() or s.casefold().startswith(needle)
    ]
    if len(fuzzy) == 1:
        return fuzzy[0]
    if len(fuzzy) > 1:
        return tuple(fuzzy)
    return None


def _display(parts: Sequence[str]) -> str:
    return " ".join(str(p) for p in parts)


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).casefold() in {"1", "true", "yes", "on"}


def _has_mod(intent: Intent, name: str) -> bool:
    return name in intent.modifiers or _truthy(intent.slots.get(name))


def _confirmed(intent: Intent) -> bool:
    return _has_mod(intent, "confirmed") or _has_mod(intent, "yes")


def _pending(
    *,
    verb: str,
    slots: Mapping[str, Any],
    materialized: tuple[str, ...],
    risk: RiskClass,
) -> PendingAction:
    return PendingAction(
        id=uuid.uuid4().hex[:12],
        verb=verb,
        slots=dict(slots),
        materialized=materialized,
        risk=risk,
        expires_at=time.time() + _PENDING_TTL_S,
    )


def _ws_fields(context: Context) -> dict[str, Any]:
    return {
        "workspace": context.workspace,
        "workspace_source": context.workspace_source or "",
    }


def _result(
    *,
    status: Status,
    summary: str,
    detail: str = "",
    evidence: tuple[str, ...] = (),
    rung: int = 4,
    pending: PendingAction | None = None,
    context: Context | None = None,
) -> Result:
    extra = _ws_fields(context) if context is not None else {}
    return Result(
        status=status,
        summary=summary,
        detail=detail or summary,
        evidence=evidence,
        rung=rung,
        pending=pending,
        **extra,
    )


def _resolve_workspace_root(context: Context) -> Path | None:
    if context.workspace is not None:
        return Path(context.workspace)
    if context.project is not None:
        return Path(context.project.root)
    return None


def _profile_for(context: Context) -> ProjectProfile | ProfileMiss:
    if context.project is not None:
        return context.project
    root = _resolve_workspace_root(context)
    if root is None:
        return ProfileMiss(root=Path("."), checked=())
    return detect_project(root)


def build_docker_verbs(
    *,
    run_fn: RunFn | None = None,
    which: WhichFn | None = None,
    get_platform: PlatformGetter | None = None,
    disambiguate: DisambiguateFn | None = None,
    list_containers: Callable[[], tuple[str, ...]] | None = None,
) -> tuple[Verb, ...]:
    """Build T4.4 container verbs with injectable seams."""

    which_fn: WhichFn = which or shutil.which
    platform_of = get_platform or (lambda: PlatformId.LINUX)

    def _require_docker(context: Context) -> Result | None:
        if which_fn(_DOCKER_BIN) is None:
            reason = missing_binary_reason(_DOCKER_BIN)
            return _result(
                status=Status.UNSUPPORTED,
                summary=reason,
                detail=reason,
                evidence=(_DOCKER_BIN,),
                context=context,
            )
        return None

    def _run(
        intent: Intent,
        context: Context,
        *,
        argv: tuple[str, ...],
        root: Path | None = None,
        timeout: float = _ONESHOT_TIMEOUT_S,
    ) -> Result:
        if "dry_run" in intent.modifiers:
            return _result(
                status=Status.DRY_RUN,
                summary=_display(argv)[:80],
                detail=_display(argv),
                evidence=argv,
                context=context,
            )
        if run_fn is None:
            return _result(
                status=Status.UNSUPPORTED,
                summary="Container runner unavailable",
                detail="docker pack requires an injected run_fn",
                evidence=argv,
                context=context,
            )
        completed = run_fn(ArgvCommand(argv=argv, cwd=root, timeout=timeout))
        code = int(getattr(completed, "returncode", 1))
        out = str(getattr(completed, "stdout", "") or "")
        err = str(getattr(completed, "stderr", "") or "")
        log_text = "\n".join(part for part in (out, err) if part)
        if code == 0:
            return _result(
                status=Status.OK,
                summary=f"Ran {_display(argv)[:60]}",
                detail=log_text[-4000:] if log_text else _display(argv),
                evidence=argv + ("exit=0",),
                context=context,
            )
        return _result(
            status=Status.FAILED,
            summary=f"Container command failed (exit {code})",
            detail=log_text[-4000:] if log_text else f"exit={code}",
            evidence=argv + (f"exit={code}",),
            context=context,
        )

    def _running_names(context: Context) -> tuple[str, ...]:
        if list_containers is not None:
            return tuple(list_containers())
        if run_fn is None:
            return ()
        completed = run_fn(
            ArgvCommand(
                argv=(_DOCKER_BIN, "ps", "--format", "{{.Names}}"),
                timeout=20.0,
            )
        )
        if int(getattr(completed, "returncode", 1)) != 0:
            return ()
        out = str(getattr(completed, "stdout", "") or "")
        names = [line.strip() for line in out.splitlines() if line.strip()]
        return tuple(names)

    def _compose_path(context: Context) -> Path | Result:
        profile = _profile_for(context)
        if isinstance(profile, ProfileMiss):
            return _result(
                status=Status.REFUSED,
                summary="No compose file",
                detail=profile.refusal_message(),
                evidence=tuple(profile.checked),
                context=context,
            )
        compose = profile.compose_file
        if compose is None:
            return _result(
                status=Status.REFUSED,
                summary="No compose file",
                detail=(
                    "No docker-compose.yml / compose.yaml in workspace. "
                    "Add one or set compose_file in vaani.toml."
                ),
                evidence=(),
                context=context,
            )
        return Path(compose)

    def _resolve_service(
        heard: str,
        compose: Path,
        context: Context,
    ) -> str | Result:
        services = list_compose_services(compose)
        if not services:
            return _result(
                status=Status.REFUSED,
                summary="No compose services",
                detail=f"No services found in {compose.name}",
                evidence=(str(compose),),
                context=context,
            )
        resolved = resolve_compose_service(heard, services)
        if resolved is None:
            options = ", ".join(services)
            return _result(
                status=Status.REFUSED,
                summary=f"Unknown service {heard!r}",
                detail=f"No compose service matches {heard!r}. Options: {options}",
                evidence=tuple(services),
                context=context,
            )
        if isinstance(resolved, tuple):
            options = ", ".join(resolved)
            if disambiguate is not None:
                chosen = disambiguate(resolved, f"Which container: {options}?")
                if chosen is not None and chosen in resolved:
                    return chosen
            return _result(
                status=Status.REFUSED,
                summary="Ambiguous container name",
                detail=(
                    f"Heard {heard!r}; matches: {options}. "
                    "Say the exact service name (disambiguation unavailable)."
                ),
                evidence=resolved,
                context=context,
            )
        return resolved

    def handle_engine_start(intent: Intent, context: Context) -> Result:
        plat = context.platform if context.platform else platform_of()
        if plat is PlatformId.MACOS:
            materialized = ("open", "-a", "Docker")
        elif plat is PlatformId.WINDOWS:
            materialized = ("cmd", "/c", "start", "", "Docker Desktop")
        else:
            # Linux: user-session docker when available.
            materialized = ("systemctl", "--user", "start", "docker")
        if "dry_run" in intent.modifiers:
            return _result(
                status=Status.DRY_RUN,
                summary=_display(materialized)[:80],
                detail=_display(materialized),
                evidence=materialized,
                rung=4,
                context=context,
            )
        # macOS/Windows launch the app; docker binary may still be missing until
        # the daemon is up — do not require docker on PATH for engine.start.
        if plat is PlatformId.LINUX:
            missing = _require_docker(context)
            # systemctl path does not need docker CLI; allow either.
            if missing is not None and which_fn("systemctl") is None:
                return missing
        return _run(intent, context, argv=materialized, timeout=60.0)

    def handle_list(intent: Intent, context: Context) -> Result:
        missing = _require_docker(context)
        if missing is not None:
            return missing
        materialized = (_DOCKER_BIN, "ps", "--format", "table {{.Names}}\t{{.Status}}\t{{.Image}}")
        if "dry_run" in intent.modifiers:
            return _result(
                status=Status.DRY_RUN,
                summary=_display(materialized)[:80],
                detail=_display(materialized),
                evidence=materialized,
                rung=4,
                context=context,
            )
        if list_containers is not None and run_fn is None:
            names = list_containers()
            detail = "\n".join(names) if names else "(none)"
            return _result(
                status=Status.OK,
                summary=f"{len(names)} container(s)",
                detail=detail,
                evidence=names,
                rung=4,
                context=context,
            )
        return _run(intent, context, argv=materialized, timeout=30.0)

    def handle_stop(intent: Intent, context: Context) -> Result:
        missing = _require_docker(context)
        if missing is not None:
            return missing
        compose = _compose_path(context)
        if isinstance(compose, Result):
            return compose
        heard = str(intent.slots.get("service") or "").strip()
        if not heard:
            return _result(
                status=Status.FAILED,
                summary="No service name",
                detail="container.stop requires a service slot",
                context=context,
            )
        service = _resolve_service(heard, compose, context)
        if isinstance(service, Result):
            return service
        root = compose.parent
        materialized = (_DOCKER_BIN, "compose", "-f", compose.name, "stop", service)
        if "dry_run" in intent.modifiers:
            return _result(
                status=Status.DRY_RUN,
                summary=_display(materialized)[:80],
                detail=_display(materialized),
                evidence=materialized,
                context=context,
            )
        return _run(intent, context, argv=materialized, root=root)

    def handle_stop_all(intent: Intent, context: Context) -> Result:
        missing = _require_docker(context)
        if missing is not None:
            return missing
        names = _running_names(context)
        if not names:
            materialized = (_DOCKER_BIN, "stop")
            if "dry_run" in intent.modifiers:
                return _result(
                    status=Status.DRY_RUN,
                    summary="No running containers",
                    detail="docker ps returned no names",
                    evidence=materialized,
                    context=context,
                )
            return _result(
                status=Status.OK,
                summary="No running containers",
                detail="Nothing to stop",
                evidence=(),
                context=context,
            )
        materialized = (_DOCKER_BIN, "stop", *names)
        listed = ", ".join(names)
        if "dry_run" in intent.modifiers:
            return _result(
                status=Status.DRY_RUN,
                summary=_display(materialized)[:80],
                detail=f"Stop {len(names)} container(s): {listed}",
                evidence=materialized,
                context=context,
            )
        if not _confirmed(intent):
            summary = f"Stop {len(names)} container(s): {listed}?"
            return _result(
                status=Status.NEEDS_CONFIRM,
                summary=summary[:80],
                detail=summary,
                evidence=materialized,
                pending=_pending(
                    verb="container.stop_all",
                    slots={"names": list(names)},
                    materialized=materialized,
                    risk=RiskClass.R3,
                ),
                context=context,
            )
        return _run(intent, context, argv=materialized, timeout=120.0)

    def handle_compose_rebuild(intent: Intent, context: Context) -> Result:
        missing = _require_docker(context)
        if missing is not None:
            return missing
        compose = _compose_path(context)
        if isinstance(compose, Result):
            return compose
        root = compose.parent
        materialized = (
            _DOCKER_BIN,
            "compose",
            "-f",
            compose.name,
            "up",
            "-d",
            "--build",
        )
        if "dry_run" in intent.modifiers:
            return _result(
                status=Status.DRY_RUN,
                summary=_display(materialized)[:80],
                detail=_display(materialized),
                evidence=materialized,
                context=context,
            )
        if not _confirmed(intent):
            summary = f"Rebuild compose stack ({compose.name})?"
            return _result(
                status=Status.NEEDS_CONFIRM,
                summary=summary[:80],
                detail=summary + f" argv: {_display(materialized)}",
                evidence=materialized,
                pending=_pending(
                    verb="container.compose.rebuild",
                    slots={"compose_file": compose.name},
                    materialized=materialized,
                    risk=RiskClass.R2,
                ),
                context=context,
            )
        return _run(intent, context, argv=materialized, root=root, timeout=900.0)

    def handle_logs(intent: Intent, context: Context) -> Result:
        missing = _require_docker(context)
        if missing is not None:
            return missing
        compose = _compose_path(context)
        if isinstance(compose, Result):
            return compose
        heard = str(intent.slots.get("service") or "").strip()
        if not heard:
            return _result(
                status=Status.FAILED,
                summary="No service name",
                detail="container.logs requires a service slot",
                context=context,
            )
        service = _resolve_service(heard, compose, context)
        if isinstance(service, Result):
            return service
        root = compose.parent
        # Tail without --follow so the runner can finish (follow is a supervisor job).
        materialized = (
            _DOCKER_BIN,
            "compose",
            "-f",
            compose.name,
            "logs",
            "--tail",
            "200",
            service,
        )
        return _run(intent, context, argv=materialized, root=root, timeout=60.0)

    return (
        Verb(
            name="container.engine.start",
            title="Start Docker engine",
            slots={},
            rung=4,
            risk=RiskClass.R1,
            requires=frozenset(),
            support=_ALL_SUPPORT,
            undo=None,
            pack=PACK_NAME,
            handler=handle_engine_start,
        ),
        Verb(
            name="container.list",
            title="List running containers",
            slots={},
            rung=4,
            risk=RiskClass.R0,
            requires=frozenset(),
            support=_ALL_SUPPORT,
            undo=None,
            pack=PACK_NAME,
            handler=handle_list,
        ),
        Verb(
            name="container.stop",
            title="Stop a compose service",
            slots={"service": SlotSpec(type="str", required=True)},
            rung=4,
            risk=RiskClass.R1,
            requires=frozenset({"workspace"}),
            support=_ALL_SUPPORT,
            undo=None,
            pack=PACK_NAME,
            handler=handle_stop,
        ),
        Verb(
            name="container.stop_all",
            title="Stop all containers",
            slots={},
            rung=4,
            risk=RiskClass.R3,
            requires=frozenset(),
            support=_ALL_SUPPORT,
            undo=None,
            pack=PACK_NAME,
            handler=handle_stop_all,
        ),
        Verb(
            name="container.compose.rebuild",
            title="Rebuild compose stack",
            slots={},
            rung=4,
            risk=RiskClass.R2,
            requires=frozenset({"workspace"}),
            support=_ALL_SUPPORT,
            undo=None,
            pack=PACK_NAME,
            handler=handle_compose_rebuild,
        ),
        Verb(
            name="container.logs",
            title="Tail container logs",
            slots={"service": SlotSpec(type="str", required=True)},
            rung=4,
            risk=RiskClass.R0,
            requires=frozenset({"workspace"}),
            support=_ALL_SUPPORT,
            undo=None,
            pack=PACK_NAME,
            handler=handle_logs,
        ),
    )


def register_docker_pack(
    registry: Registry,
    *,
    run_fn: RunFn | None = None,
    which: WhichFn | None = None,
    get_platform: PlatformGetter | None = None,
    disambiguate: DisambiguateFn | None = None,
    list_containers: Callable[[], tuple[str, ...]] | None = None,
) -> tuple[Pattern, ...]:
    """Register ``container.*`` verbs; return grammar patterns."""
    for verb in build_docker_verbs(
        run_fn=run_fn,
        which=which,
        get_platform=get_platform,
        disambiguate=disambiguate,
        list_containers=list_containers,
    ):
        registry.register(verb)
    return docker_patterns()
