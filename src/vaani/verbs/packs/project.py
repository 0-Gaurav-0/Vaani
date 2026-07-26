"""T3.4 pack: project commands, managed jobs, terminal/editor open."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from vaani.context.project import ProfileMiss, detect_project, require_command
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
from vaani.platform.protocol import PlatformId, ProcInfo
from vaani.verbs.registry import Registry

# L3 must not import L5 (exec). Supervisor/runner are injected; Command is a
# duck-typed local argv carrier compatible with Supervisor.start / runner.run.

PROJECT_VERB_NAMES: frozenset[str] = frozenset(
    {
        "project.dev.start",
        "project.dev.stop",
        "project.dev.restart",
        "project.test.run",
        "project.build",
        "project.typecheck",
        "project.deps.install",
        "project.format",
        "job.list",
        "job.logs",
        "app.terminal.open",
        "editor.open",
    }
)

_ALL_SUPPORT = {
    PlatformId.LINUX: Support.SUPPORTED,
    PlatformId.MACOS: Support.SUPPORTED,
    PlatformId.WINDOWS: Support.SUPPORTED,
}

_DEV_JOB_KEY = "project.dev"
_PENDING_TTL_S = 20.0
_DEFAULT_DEV_PORTS: tuple[int, ...] = (3000, 5173, 8080, 8000, 4200, 5000, 8765)
_ONESHOT_TIMEOUT_S = 600.0

# pytest / jest / vitest-ish failure lines
_FAILED_LINE_RE = re.compile(
    r"^(?:FAILED|ERROR|FAIL)\s+(\S+?)(?:\s+-|$)",
    re.MULTILINE,
)
_FILE_LINE_RE = re.compile(
    r"(?P<path>(?:[A-Za-z]:)?[^\s:\"']+\.[A-Za-z0-9_+-]+):(?P<line>\d+)"
)

_MANAGER_INSTALL: dict[str, tuple[str, ...]] = {
    "npm": ("npm", "install"),
    "pnpm": ("pnpm", "install"),
    "yarn": ("yarn", "install"),
    "bun": ("bun", "install"),
    "uv": ("uv", "sync"),
    "poetry": ("poetry", "install"),
    "pipenv": ("pipenv", "install"),
    "pip": ("pip", "install", "-r", "requirements.txt"),
    "hatch": ("hatch", "env", "create"),
}

_PORT_RE = re.compile(r"https?://(?:localhost|127\.0\.0\.1):(\d+)", re.IGNORECASE)

SupervisorGetter = Callable[[], Any | None]
SystemGetter = Callable[[], Any | None]
TerminalGetter = Callable[[], Any | None]
PlatformGetter = Callable[[], PlatformId]
CancelGetter = Callable[[], threading.Event | None]
RunFn = Callable[..., Any]
Popen = Callable[..., Any]


@dataclass(frozen=True)
class ArgvCommand:
    """Duck-typed argv carrier for injected Supervisor/runner (no L5 import)."""

    argv: tuple[str, ...]
    cwd: Path | None = None
    timeout: float = 20.0
    env_extra: Mapping[str, str] = field(default_factory=dict)


@dataclass
class LastRun:
    """Most recent one-shot project command (for UI-EDIT-04)."""

    key: str
    verb: str
    argv: tuple[str, ...]
    cwd: str
    returncode: int
    log_text: str
    started_at: float = field(default_factory=time.time)


def _discover_port(log_text: str) -> int | None:
    """First localhost URL port in the first 200 lines (mirrors supervisor)."""
    for index, line in enumerate(log_text.splitlines()):
        if index >= 200:
            break
        match = _PORT_RE.search(line)
        if match is not None:
            try:
                return int(match.group(1))
            except ValueError:
                continue
    return None

def project_patterns() -> tuple[Pattern, ...]:
    """Grammar rows for TERM-DEV / TERM-TEST / TERM-JOB / UI-EDIT / TERM-SESS."""
    return (
        Pattern(
            verb="project.dev.start",
            any_of=(
                (
                    "start the dev server",
                    "start dev server",
                    "run the dev server",
                    "start the development server",
                ),
            ),
            exact=True,
            priority=55,
        ),
        Pattern(
            verb="project.dev.stop",
            any_of=(
                (
                    "stop the dev server",
                    "stop dev server",
                    "kill the dev server",
                ),
            ),
            exact=True,
            priority=55,
        ),
        Pattern(
            verb="project.dev.restart",
            any_of=(
                (
                    "restart the dev server",
                    "restart dev server",
                ),
            ),
            exact=True,
            priority=55,
        ),
        Pattern(
            verb="project.test.run",
            any_of=(
                (
                    "run the tests",
                    "run the test suite",
                    "run tests",
                    "run the unit tests",
                    "run unit tests",
                    "run the unit tests only",
                ),
            ),
            exclude=("fix the", "failing test", "why are"),
            priority=54,
            fixed_slots={"scope": "all"},
        ),
        Pattern(
            verb="project.test.run",
            any_of=(("run the unit tests only", "run unit tests only"),),
            exact=True,
            priority=56,
            fixed_slots={"scope": "unit"},
        ),
        Pattern(
            verb="project.test.run",
            any_of=(("watch the tests", "watch tests"),),
            exact=True,
            priority=56,
            fixed_slots={"watch": True},
        ),
        Pattern(
            verb="project.deps.install",
            any_of=(
                (
                    "install the dependencies",
                    "install dependencies",
                    "install deps",
                    "npm install",
                    "pnpm install",
                ),
            ),
            exact=True,
            priority=54,
        ),
        Pattern(
            verb="project.build",
            any_of=(
                (
                    "build the project",
                    "build project",
                    "run the build",
                ),
            ),
            exact=True,
            priority=54,
        ),
        Pattern(
            verb="project.typecheck",
            any_of=(
                (
                    "typecheck the project",
                    "type check the project",
                    "run the typecheck",
                    "run typecheck",
                ),
            ),
            exact=True,
            priority=54,
        ),
        Pattern(
            verb="project.format",
            any_of=(
                (
                    "format this file",
                    "format the file",
                    "format file",
                    "format the current file",
                    "format the project",
                    "run the formatter",
                ),
            ),
            exact=True,
            # Higher than editor.format (rung 7) — T5.3 ladder preference.
            priority=60,
        ),
        Pattern(
            verb="job.list",
            any_of=(
                (
                    "what's running",
                    "what is running",
                    "show running jobs",
                    "list jobs",
                ),
            ),
            exact=True,
            priority=50,
        ),
        Pattern(
            verb="job.logs",
            any_of=(
                (
                    "show the dev server logs",
                    "show dev server logs",
                    "show the job logs",
                    "show job logs",
                ),
            ),
            exact=True,
            priority=52,
            fixed_slots={"key": _DEV_JOB_KEY},
        ),
        Pattern(
            verb="app.terminal.open",
            any_of=(
                (
                    "open a terminal in this project",
                    "open terminal in this project",
                    "open a terminal here",
                    "open terminal here",
                ),
            ),
            exact=True,
            priority=53,
        ),
        Pattern(
            verb="editor.open",
            any_of=(
                (
                    "open the file with the build error",
                    "open the file with the error",
                    "open the build error",
                ),
            ),
            exact=True,
            priority=58,
            fixed_slots={"from_error": True},
        ),
        Pattern(
            verb="editor.open",
            any_of=(
                (
                    "open this folder in cursor",
                    "open this in cursor",
                    "open my project in cursor",
                    "open this project in cursor",
                ),
            ),
            exact=True,
            priority=57,
            fixed_slots={"editor": "cursor"},
        ),
        Pattern(
            verb="editor.open",
            any_of=(
                (
                    "open this folder in vs code",
                    "open this in vs code",
                    "open this in vscode",
                    "open this project in vs code",
                ),
            ),
            exact=True,
            priority=57,
            fixed_slots={"editor": "code"},
        ),
    )


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
    rung: int = 3,
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


def _profile_for(context: Context) -> ProjectProfile | ProfileMiss:
    if context.project is not None:
        return context.project
    if context.workspace is None:
        return ProfileMiss(root=Path("."), checked=())
    return detect_project(context.workspace)


def _command_from_context(
    context: Context,
    field: str,
) -> tuple[str, ...] | ProfileMiss:
    """Prefer Context.project argv; otherwise detect from workspace root."""
    if field not in {"test", "build", "dev", "typecheck", "lint", "format"}:
        raise ValueError(f"unknown project command field: {field!r}")
    if context.project is not None:
        argv = getattr(context.project, field, None)
        if field == "format" and not argv:
            # Prefer dedicated format; lint is a weaker verifiable fallback.
            argv = context.project.lint
        if argv:
            return tuple(argv)
        return ProfileMiss(root=Path(context.project.root))
    root = _resolve_workspace_root(context)
    if root is None:
        return ProfileMiss(root=Path("."), checked=())
    if field == "format":
        detected = detect_project(root)
        if isinstance(detected, ProjectProfile):
            argv = detected.format or detected.lint
            if argv:
                return tuple(argv)
            return ProfileMiss(root=detected.root)
        return detected
    return require_command(root, field)


def _refuse_miss(miss: ProfileMiss, *, context: Context, field: str | None = None) -> Result:
    msg = miss.refusal_message()
    if field:
        msg = f"No {field} command detected. {msg}"
    checked = ", ".join(miss.checked) if miss.checked else "(no workspace)"
    return _result(
        status=Status.REFUSED,
        summary=f"No project profile ({field or 'tooling'})",
        detail=f"{msg} Checked: {checked}.",
        evidence=tuple(miss.checked),
        rung=3,
        context=context,
    )


def _resolve_workspace_root(context: Context) -> Path | None:
    if context.workspace is not None:
        return Path(context.workspace)
    if context.project is not None:
        return Path(context.project.root)
    return None

def _install_argv(profile: ProjectProfile) -> tuple[str, ...] | None:
    if profile.manager is None:
        return None
    return _MANAGER_INSTALL.get(profile.manager)


def _augment_test_argv(
    argv: tuple[str, ...],
    *,
    scope: str | None,
    filter_text: str | None,
    watch: bool,
) -> tuple[str, ...]:
    out = list(argv)
    head = out[0].casefold() if out else ""
    if watch:
        if head == "pytest":
            out.append("--looponfail")
        elif head in {"npm", "pnpm", "yarn", "bun"} and "test" in out:
            # Best-effort; many scripts ignore unknown flags.
            out.extend(["--", "--watch"])
    if scope == "unit" and head == "pytest":
        out.append("tests/unit")
    elif scope == "integration" and head == "pytest":
        out.append("tests/integration")
    if filter_text:
        if head == "pytest":
            out.extend(["-k", filter_text])
        elif head in {"npm", "pnpm", "yarn", "bun"}:
            out.extend(["--", "-t", filter_text])
    return tuple(out)


def parse_failing_names(log_text: str) -> tuple[str, ...]:
    names: list[str] = []
    seen: set[str] = set()
    for match in _FAILED_LINE_RE.finditer(log_text):
        name = match.group(1).strip()
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return tuple(names)


def parse_file_line(log_text: str) -> tuple[str, int] | None:
    """First ``file:line`` reference from a failing job log (UI-EDIT-04)."""
    # Prefer traceback-style paths near FAILED/Error markers.
    for line in log_text.splitlines():
        folded = line.casefold()
        if "error" not in folded and "failed" not in folded and "traceback" not in folded:
            # Still accept plain compiler diagnostics.
            if not re.search(r"\.(py|ts|tsx|js|jsx|go|rs|java):", line):
                continue
        match = _FILE_LINE_RE.search(line)
        if match is None:
            continue
        path = match.group("path")
        # Skip URLs / node internals noise.
        if path.startswith("http") or "node_modules" in path:
            continue
        try:
            return path, int(match.group("line"))
        except ValueError:
            continue
    # Fallback: first file:line anywhere.
    match = _FILE_LINE_RE.search(log_text)
    if match is None:
        return None
    try:
        return match.group("path"), int(match.group("line"))
    except ValueError:
        return None


def _format_holders(holders: Sequence[ProcInfo]) -> str:
    parts = [f"{h.name} (PID {h.pid})" for h in holders]
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts)


def _port_materialized(holders: Sequence[ProcInfo], signal: str = "term") -> tuple[str, ...]:
    if not holders:
        return ("port.free", "already-free")
    return ("kill", "-TERM", *[str(h.pid) for h in holders])


def _attach_dry_run(handler: Callable[[Intent, Context], Result]) -> Callable[[Intent, Context], Result]:
    def dry_run(intent: Intent, context: Context) -> Result:
        return handler(
            Intent(
                verb=intent.verb,
                slots=intent.slots,
                rung=intent.rung,
                confidence=intent.confidence,
                source=intent.source,
                mode=intent.mode,
                utterance=intent.utterance,
                raw_utterance=intent.raw_utterance,
                modifiers=frozenset(set(intent.modifiers) | {"dry_run"}),
                brain=intent.brain,
            ),
            context,
        )

    handler.dry_run = dry_run  # type: ignore[attr-defined]
    return handler


def build_project_verbs(
    *,
    get_supervisor: SupervisorGetter | None = None,
    get_system: SystemGetter | None = None,
    get_terminal: TerminalGetter | None = None,
    get_platform: PlatformGetter | None = None,
    get_cancel: CancelGetter | None = None,
    run_fn: RunFn | None = None,
    popen: Popen | None = None,
    last_runs: list[LastRun] | None = None,
) -> tuple[Verb, ...]:
    """Build T3.4 project/job/editor verbs with injectable seams."""

    platform_of = get_platform or (lambda: PlatformId.LINUX)
    runner = run_fn
    spawn = popen or subprocess.Popen
    memory: list[LastRun] = last_runs if last_runs is not None else []

    def _supervisor() -> Any | None:
        return get_supervisor() if get_supervisor is not None else None

    def _remember(run: LastRun) -> None:
        memory.append(run)
        # Keep a small ring so tests / UI-EDIT-04 stay bounded.
        del memory[:-8]

    def _last_failing() -> LastRun | None:
        for run in reversed(memory):
            if run.returncode != 0:
                return run
        return None

    def handle_dev_start(intent: Intent, context: Context) -> Result:
        root = _resolve_workspace_root(context)
        if root is None:
            return _result(
                status=Status.REFUSED,
                summary="No workspace",
                detail="project.dev.start requires a workspace",
                context=context,
            )
        cmd = _command_from_context(context, "dev")
        if isinstance(cmd, ProfileMiss):
            return _refuse_miss(cmd, context=context, field="dev")
        materialized = tuple(cmd)
        if "dry_run" in intent.modifiers:
            return _result(
                status=Status.DRY_RUN,
                summary=_display(materialized)[:80],
                detail=_display(materialized),
                evidence=materialized,
                context=context,
            )
        sup = _supervisor()
        if sup is None:
            return _result(
                status=Status.UNSUPPORTED,
                summary="Supervisor unavailable",
                detail="project.dev.start requires a process supervisor",
                evidence=materialized,
                context=context,
            )
        existing = sup.status(_DEV_JOB_KEY)
        if existing is not None and existing.status == "running":
            port_bit = f" on port {existing.port}" if existing.port else ""
            return _result(
                status=Status.OK,
                summary=f"Dev server already running{port_bit}",
                detail=f"pid={existing.pid} argv={_display(existing.argv)}",
                evidence=existing.argv,
                context=context,
            )
        try:
            job = sup.start(
                _DEV_JOB_KEY,
                ArgvCommand(argv=materialized, cwd=root),
                verb="project.dev.start",
            )
        except ValueError as exc:
            return _result(
                status=Status.FAILED,
                summary="Could not start dev server",
                detail=str(exc),
                evidence=materialized,
                context=context,
            )
        # Brief wait for port discovery from logs.
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline:
            refreshed = sup.status(_DEV_JOB_KEY)
            if refreshed is not None and refreshed.port is not None:
                job = refreshed
                break
            time.sleep(0.05)
        port_bit = f" at http://127.0.0.1:{job.port}" if job.port else ""
        return _result(
            status=Status.OK,
            summary=f"Started dev server{port_bit}",
            detail=f"pid={job.pid} {_display(job.argv)}",
            evidence=job.argv,
            context=context,
        )

    def handle_dev_stop(intent: Intent, context: Context) -> Result:
        if "dry_run" in intent.modifiers:
            return _result(
                status=Status.DRY_RUN,
                summary="supervisor.stop project.dev",
                detail="Stop managed dev job or free held port",
                evidence=("supervisor.stop", _DEV_JOB_KEY),
                context=context,
            )
        sup = _supervisor()
        if sup is not None:
            job = sup.status(_DEV_JOB_KEY)
            if job is not None and job.status == "running":
                stopped = sup.stop(_DEV_JOB_KEY, forceful=_has_mod(intent, "force"))
                if stopped:
                    return _result(
                        status=Status.OK,
                        summary="Stopped dev server",
                        detail=f"Stopped pid={job.pid}",
                        evidence=job.argv,
                        context=context,
                    )
                return _result(
                    status=Status.FAILED,
                    summary="Could not stop dev server",
                    detail=f"supervisor.stop returned false for {_DEV_JOB_KEY}",
                    evidence=job.argv,
                    context=context,
                )

        # Unmanaged: fall back to system.port.free with R2 confirm.
        system = get_system() if get_system is not None else None
        if system is None:
            return _result(
                status=Status.FAILED,
                summary="No managed dev server",
                detail=(
                    "No Vaani-managed project.dev job, and SystemControl is "
                    "unavailable for port-based stop"
                ),
                evidence=("project.dev.stop",),
                context=context,
            )

        port = intent.slots.get("port")
        try:
            port_i = int(port) if port is not None else None
        except (TypeError, ValueError):
            port_i = None

        holders: tuple[ProcInfo, ...] = ()
        chosen_port: int | None = port_i
        if chosen_port is not None:
            holders = tuple(system.list_listeners(chosen_port))
        else:
            found: list[tuple[int, tuple[ProcInfo, ...]]] = []
            for candidate in _DEFAULT_DEV_PORTS:
                found_holders = tuple(system.list_listeners(candidate))
                if found_holders:
                    found.append((candidate, found_holders))
            if len(found) == 1:
                chosen_port, holders = found[0]
            elif len(found) > 1:
                listed = "; ".join(
                    f"{p}: {_format_holders(hs)}" for p, hs in found
                )
                return _result(
                    status=Status.REFUSED,
                    summary="Multiple ports held",
                    detail=(
                        "No managed dev job; multiple common ports have listeners: "
                        f"{listed}. Pass port=… to choose."
                    ),
                    evidence=tuple(str(p) for p, _ in found),
                    context=context,
                )

        if chosen_port is None or not holders:
            return _result(
                status=Status.OK,
                summary="No managed dev server",
                detail="No project.dev job and no common ports held",
                evidence=("project.dev.stop", "noop"),
                context=context,
            )

        materialized = _port_materialized(holders)
        summary = (
            f"No managed job — kill {_format_holders(holders)} "
            f"on port {chosen_port}?"
        )
        if not _confirmed(intent):
            return _result(
                status=Status.NEEDS_CONFIRM,
                summary=summary[:80],
                detail=summary,
                evidence=materialized,
                pending=_pending(
                    verb="project.dev.stop",
                    slots={"port": chosen_port, "signal": "term"},
                    materialized=materialized,
                    risk=RiskClass.R2,
                ),
                context=context,
            )
        kill_result = system.kill_pids([h.pid for h in holders], signal="term")
        if kill_result.status is Status.OK:
            return _result(
                status=Status.OK,
                summary=f"Freed port {chosen_port}",
                detail=f"Signaled {_format_holders(holders)} (unmanaged)",
                evidence=materialized,
                context=context,
            )
        return _result(
            status=kill_result.status,
            summary=kill_result.summary or f"Could not free port {chosen_port}",
            detail=kill_result.detail,
            evidence=materialized,
            context=context,
        )

    def handle_dev_restart(intent: Intent, context: Context) -> Result:
        root = _resolve_workspace_root(context)
        if root is None:
            return _result(
                status=Status.REFUSED,
                summary="No workspace",
                detail="project.dev.restart requires a workspace",
                context=context,
            )
        cmd = _command_from_context(context, "dev")
        if isinstance(cmd, ProfileMiss):
            return _refuse_miss(cmd, context=context, field="dev")
        materialized = tuple(cmd)
        if "dry_run" in intent.modifiers:
            return _result(
                status=Status.DRY_RUN,
                summary=_display(materialized)[:80],
                detail=f"restart → {_display(materialized)}",
                evidence=materialized,
                context=context,
            )
        sup = _supervisor()
        if sup is None:
            return _result(
                status=Status.UNSUPPORTED,
                summary="Supervisor unavailable",
                detail="project.dev.restart requires a process supervisor",
                evidence=materialized,
                context=context,
            )
        existing = sup.status(_DEV_JOB_KEY)
        argv_changed = False
        if existing is not None and existing.argv != materialized:
            argv_changed = True
            # Profile changed — stop old and start fresh with new argv.
            if existing.status == "running":
                sup.stop(_DEV_JOB_KEY)
            job = sup.start(
                _DEV_JOB_KEY,
                ArgvCommand(argv=materialized, cwd=root),
                verb="project.dev.start",
            )
        elif existing is not None:
            try:
                job = sup.restart(_DEV_JOB_KEY)
            except KeyError:
                job = sup.start(
                    _DEV_JOB_KEY,
                    ArgvCommand(argv=materialized, cwd=root),
                    verb="project.dev.start",
                )
        else:
            job = sup.start(
                _DEV_JOB_KEY,
                ArgvCommand(argv=materialized, cwd=root),
                verb="project.dev.start",
            )
        note = " (argv changed)" if argv_changed else ""
        port_bit = f" port={job.port}" if job.port else ""
        return _result(
            status=Status.OK,
            summary=f"Restarted dev server{note}",
            detail=f"pid={job.pid}{port_bit} {_display(job.argv)}",
            evidence=job.argv,
            context=context,
        )

    def _run_oneshot(
        intent: Intent,
        context: Context,
        *,
        field: str,
        verb: str,
        job_key: str,
        argv: tuple[str, ...],
        root: Path,
        timeout: float = _ONESHOT_TIMEOUT_S,
        rung: int = 3,
    ) -> Result:
        if "dry_run" in intent.modifiers:
            return _result(
                status=Status.DRY_RUN,
                summary=_display(argv)[:80],
                detail=_display(argv),
                evidence=argv,
                context=context,
                rung=rung,
            )
        if runner is None:
            return _result(
                status=Status.UNSUPPORTED,
                summary=f"{field} runner unavailable",
                detail="project pack requires an injected run_fn",
                evidence=argv,
                context=context,
                rung=rung,
            )
        cancel = get_cancel() if get_cancel is not None else None
        completed = runner(
            ArgvCommand(argv=argv, cwd=root, timeout=timeout),
            cancel=cancel,
        )
        log_text = "\n".join(
            part for part in (completed.stdout, completed.stderr) if part
        )
        _remember(
            LastRun(
                key=job_key,
                verb=verb,
                argv=argv,
                cwd=os.fspath(root),
                returncode=completed.returncode,
                log_text=log_text,
            )
        )
        if completed.cancelled:
            return _result(
                status=Status.FAILED,
                summary=f"{field} cancelled",
                detail="Cancelled mid-run",
                evidence=argv + (f"exit={completed.returncode}",),
                context=context,
                rung=rung,
            )
        if completed.timed_out:
            return _result(
                status=Status.FAILED,
                summary=f"{field} timed out",
                detail=f"Timed out after {timeout:.0f}s",
                evidence=argv + ("timed_out",),
                context=context,
                rung=rung,
            )
        failing = parse_failing_names(log_text)
        if completed.returncode == 0:
            return _result(
                status=Status.OK,
                summary=f"{field} passed (exit 0)",
                detail=log_text[-4000:] if log_text else f"{field} ok",
                evidence=argv + ("exit=0",),
                context=context,
                rung=rung,
            )
        fail_bit = f"; failing: {', '.join(failing)}" if failing else ""
        detail_parts = [
            f"exit={completed.returncode}{fail_bit}",
        ]
        if failing:
            detail_parts.append("failures=" + ",".join(failing))
        if log_text:
            detail_parts.append(log_text[-4000:])
        return _result(
            status=Status.FAILED,
            summary=f"{field} failed (exit {completed.returncode})",
            detail="\n".join(detail_parts),
            evidence=argv + (f"exit={completed.returncode}", *failing),
            context=context,
            rung=rung,
        )

    def handle_test_run(intent: Intent, context: Context) -> Result:
        root = _resolve_workspace_root(context)
        if root is None:
            return _result(
                status=Status.REFUSED,
                summary="No workspace",
                detail="project.test.run requires a workspace",
                context=context,
            )
        cmd = _command_from_context(context, "test")
        if isinstance(cmd, ProfileMiss):
            return _refuse_miss(cmd, context=context, field="test")
        scope = intent.slots.get("scope")
        scope_s = str(scope) if scope is not None else None
        filter_raw = intent.slots.get("filter")
        filter_s = str(filter_raw) if filter_raw else None
        watch = _truthy(intent.slots.get("watch"))
        argv = _augment_test_argv(
            tuple(cmd), scope=scope_s, filter_text=filter_s, watch=watch
        )
        if watch:
            if "dry_run" in intent.modifiers:
                return _result(
                    status=Status.DRY_RUN,
                    summary=_display(argv)[:80],
                    detail=_display(argv),
                    evidence=argv,
                    context=context,
                )
            sup = _supervisor()
            if sup is None:
                return _result(
                    status=Status.UNSUPPORTED,
                    summary="Supervisor unavailable",
                    detail="watch mode needs the process supervisor",
                    evidence=argv,
                    context=context,
                )
            try:
                job = sup.start(
                    "project.test",
                    ArgvCommand(argv=argv, cwd=root),
                    verb="project.test.run",
                )
            except ValueError as exc:
                return _result(
                    status=Status.FAILED,
                    summary="Could not start test watch",
                    detail=str(exc),
                    evidence=argv,
                    context=context,
                )
            return _result(
                status=Status.OK,
                summary="Watching tests",
                detail=f"pid={job.pid} {_display(job.argv)}",
                evidence=job.argv,
                context=context,
            )
        return _run_oneshot(
            intent,
            context,
            field="Tests",
            verb="project.test.run",
            job_key="project.test",
            argv=argv,
            root=root,
        )

    def handle_build(intent: Intent, context: Context) -> Result:
        root = _resolve_workspace_root(context)
        if root is None:
            return _result(
                status=Status.REFUSED,
                summary="No workspace",
                detail="project.build requires a workspace",
                context=context,
            )
        cmd = _command_from_context(context, "build")
        if isinstance(cmd, ProfileMiss):
            return _refuse_miss(cmd, context=context, field="build")
        return _run_oneshot(
            intent,
            context,
            field="Build",
            verb="project.build",
            job_key="project.build",
            argv=tuple(cmd),
            root=root,
        )

    def handle_typecheck(intent: Intent, context: Context) -> Result:
        root = _resolve_workspace_root(context)
        if root is None:
            return _result(
                status=Status.REFUSED,
                summary="No workspace",
                detail="project.typecheck requires a workspace",
                context=context,
            )
        cmd = _command_from_context(context, "typecheck")
        if isinstance(cmd, ProfileMiss):
            return _refuse_miss(cmd, context=context, field="typecheck")
        return _run_oneshot(
            intent,
            context,
            field="Typecheck",
            verb="project.typecheck",
            job_key="project.typecheck",
            argv=tuple(cmd),
            root=root,
        )

    def handle_format(intent: Intent, context: Context) -> Result:
        """Rung-4 verifiable formatter (T5.3) — preferred over editor.format."""
        root = _resolve_workspace_root(context)
        if root is None:
            return _result(
                status=Status.REFUSED,
                summary="No workspace",
                detail="project.format requires a workspace",
                context=context,
                rung=4,
            )
        cmd = _command_from_context(context, "format")
        if isinstance(cmd, ProfileMiss):
            miss = _refuse_miss(cmd, context=context, field="format")
            return _result(
                status=miss.status,
                summary=miss.summary,
                detail=miss.detail,
                evidence=miss.evidence,
                context=context,
                rung=4,
            )
        return _run_oneshot(
            intent,
            context,
            field="Format",
            verb="project.format",
            job_key="project.format",
            argv=tuple(cmd),
            root=root,
            rung=4,
        )

    def handle_deps_install(intent: Intent, context: Context) -> Result:
        root = _resolve_workspace_root(context)
        if root is None:
            return _result(
                status=Status.REFUSED,
                summary="No workspace",
                detail="project.deps.install requires a workspace",
                context=context,
            )
        profile = _profile_for(context)
        if isinstance(profile, ProfileMiss):
            return _refuse_miss(profile, context=context, field="deps")
        argv = _install_argv(profile)
        if argv is None:
            return _refuse_miss(
                ProfileMiss(root=profile.root),
                context=context,
                field="deps",
            )
        return _run_oneshot(
            intent,
            context,
            field="Deps install",
            verb="project.deps.install",
            job_key="project.deps",
            argv=argv,
            root=root,
        )

    def handle_job_list(intent: Intent, context: Context) -> Result:
        if "dry_run" in intent.modifiers:
            return _result(
                status=Status.DRY_RUN,
                summary="job.list",
                detail="List managed supervisor jobs",
                evidence=("job.list",),
                rung=1,
                context=context,
            )
        sup = _supervisor()
        if sup is None:
            return _result(
                status=Status.UNSUPPORTED,
                summary="Supervisor unavailable",
                detail="job.list requires a process supervisor",
                evidence=("job.list",),
                rung=1,
                context=context,
            )
        jobs = sup.list_jobs()
        if not jobs:
            return _result(
                status=Status.OK,
                summary="No managed jobs",
                detail="Supervisor registry is empty",
                evidence=(),
                rung=1,
                context=context,
            )
        lines = [
            f"{j.key}: {j.status} pid={j.pid}"
            + (f" port={j.port}" if j.port else "")
            + f" {_display(j.argv)}"
            for j in jobs
        ]
        return _result(
            status=Status.OK,
            summary=f"{len(jobs)} job(s)",
            detail="\n".join(lines),
            evidence=tuple(j.key for j in jobs),
            rung=1,
            context=context,
        )

    def handle_job_logs(intent: Intent, context: Context) -> Result:
        key = str(intent.slots.get("key") or _DEV_JOB_KEY)
        tail_raw = intent.slots.get("tail", 200)
        try:
            tail = int(tail_raw)
        except (TypeError, ValueError):
            tail = 200
        if "dry_run" in intent.modifiers:
            return _result(
                status=Status.DRY_RUN,
                summary=f"job.logs {key}",
                detail=f"Tail {tail} lines of {key}",
                evidence=("job.logs", key, str(tail)),
                rung=1,
                context=context,
            )
        sup = _supervisor()
        if sup is None:
            return _result(
                status=Status.UNSUPPORTED,
                summary="Supervisor unavailable",
                detail="job.logs requires a process supervisor",
                evidence=("job.logs", key),
                rung=1,
                context=context,
            )
        text = sup.logs(key, tail=tail)
        if not text:
            return _result(
                status=Status.FAILED,
                summary=f"No logs for {key}",
                detail="Job missing or log empty",
                evidence=("job.logs", key),
                rung=1,
                context=context,
            )
        # Surface discovered URL if present.
        port = _discover_port(text)
        summary = f"Logs for {key}" + (f" (port {port})" if port else "")
        return _result(
            status=Status.OK,
            summary=summary,
            detail=text[-8000:],
            evidence=("job.logs", key),
            rung=1,
            context=context,
        )

    def handle_terminal_open(intent: Intent, context: Context) -> Result:
        root = _resolve_workspace_root(context)
        if root is None:
            return _result(
                status=Status.REFUSED,
                summary="No workspace",
                detail="app.terminal.open requires a workspace",
                rung=3,
                context=context,
            )
        cwd = os.fspath(root.resolve())
        terminal = get_terminal() if get_terminal is not None else None
        platform = context.platform if context.platform else platform_of()
        argv = _terminal_argv(platform, cwd)
        if "dry_run" in intent.modifiers:
            evidence = argv if argv else ("app.terminal.open", cwd)
            return _result(
                status=Status.DRY_RUN,
                summary=_display(evidence)[:80],
                detail=_display(evidence),
                evidence=evidence,
                rung=3,
                context=context,
            )
        if terminal is not None and hasattr(terminal, "open"):
            try:
                opened = terminal.open(cwd)
                if isinstance(opened, Result):
                    return opened
                return _result(
                    status=Status.OK,
                    summary=f"Opened terminal at {root.name}",
                    detail=cwd,
                    evidence=argv or ("app.terminal.open", cwd),
                    rung=3,
                    context=context,
                )
            except Exception as exc:
                return _result(
                    status=Status.FAILED,
                    summary="Terminal open failed",
                    detail=str(exc),
                    evidence=argv or ("app.terminal.open", cwd),
                    rung=3,
                    context=context,
                )
        if argv is None:
            return _result(
                status=Status.UNSUPPORTED,
                summary="Terminal open unsupported",
                detail=f"No terminal opener for {platform.value}",
                evidence=("app.terminal.open", cwd),
                rung=3,
                context=context,
            )
        # Best-effort: require the primary binary when it is a which-able name.
        primary = argv[0]
        if primary not in {"open", "cmd", "wt"} and shutil.which(primary) is None:
            return _result(
                status=Status.UNSUPPORTED,
                summary="Terminal binary missing",
                detail=f"{primary!r} not found on PATH",
                evidence=argv,
                rung=3,
                context=context,
            )
        try:
            spawn(
                list(argv),
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            return _result(
                status=Status.FAILED,
                summary="Could not open terminal",
                detail=str(exc),
                evidence=argv,
                rung=3,
                context=context,
            )
        return _result(
            status=Status.OK,
            summary=f"Opened terminal at {root.name}",
            detail=cwd,
            evidence=argv,
            rung=3,
            context=context,
        )

    def handle_editor_open(intent: Intent, context: Context) -> Result:
        platform = context.platform if context.platform else platform_of()
        from_error = _truthy(intent.slots.get("from_error"))
        path_raw = intent.slots.get("path")
        line_raw = intent.slots.get("line")
        editor = str(intent.slots.get("editor") or "cursor").casefold()

        path: Path | None = None
        line: int | None = None
        if from_error or (path_raw is None and "build error" in intent.utterance.casefold()):
            failing = _last_failing()
            log_text = failing.log_text if failing is not None else ""
            if not log_text:
                # Fall back to supervisor job logs for build/test.
                sup = _supervisor()
                if sup is not None:
                    for key in ("project.build", "project.test", "project.typecheck", _DEV_JOB_KEY):
                        text = sup.logs(key, tail=400)
                        if text:
                            log_text = text
                            break
            parsed = parse_file_line(log_text) if log_text else None
            if parsed is None:
                return _result(
                    status=Status.FAILED,
                    summary="No file:line in last job log",
                    detail="Could not parse a file:line from the last failing job",
                    evidence=("editor.open", "from_error"),
                    rung=3,
                    context=context,
                )
            rel, line = parsed
            root = _resolve_workspace_root(context)
            candidate = Path(rel)
            if not candidate.is_absolute() and root is not None:
                candidate = root / rel
            path = candidate
        else:
            if path_raw is not None:
                path = Path(str(path_raw))
            else:
                root = _resolve_workspace_root(context)
                if root is None:
                    return _result(
                        status=Status.REFUSED,
                        summary="No workspace",
                        detail="editor.open requires path or workspace",
                        rung=3,
                        context=context,
                    )
                path = root
            if line_raw is not None:
                try:
                    line = int(line_raw)
                except (TypeError, ValueError):
                    line = None

        assert path is not None
        argv = _editor_argv(platform, editor, path, line)
        if "dry_run" in intent.modifiers:
            evidence = argv if argv else ("editor.open", str(path))
            return _result(
                status=Status.DRY_RUN,
                summary=_display(evidence)[:80],
                detail=_display(evidence),
                evidence=evidence,
                rung=3,
                context=context,
            )
        if argv is None:
            return _result(
                status=Status.UNSUPPORTED,
                summary="Editor open unsupported",
                detail=f"No opener for editor={editor!r} on {platform.value}",
                evidence=("editor.open", editor, str(path)),
                rung=3,
                context=context,
            )
        primary = argv[0]
        if primary not in {"open", "cmd"} and shutil.which(primary) is None:
            # Try alternate editors before giving up.
            for alt in ("cursor", "code", "zed"):
                if alt == editor:
                    continue
                alt_argv = _editor_argv(platform, alt, path, line)
                if alt_argv and (
                    alt_argv[0] in {"open"} or shutil.which(alt_argv[0])
                ):
                    argv = alt_argv
                    editor = alt
                    break
            else:
                return _result(
                    status=Status.UNSUPPORTED,
                    summary="Editor binary missing",
                    detail=f"{primary!r} not found; no alternate editor on PATH",
                    evidence=argv,
                    rung=3,
                    context=context,
                )
        try:
            spawn(
                list(argv),
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            return _result(
                status=Status.FAILED,
                summary="Could not open editor",
                detail=str(exc),
                evidence=argv,
                rung=3,
                context=context,
            )
        loc = f"{path}:{line}" if line is not None else str(path)
        return _result(
            status=Status.OK,
            summary=f"Opened {loc} in {editor}",
            detail=loc,
            evidence=argv,
            rung=3,
            context=context,
        )

    handlers = (
        handle_dev_start,
        handle_dev_stop,
        handle_dev_restart,
        handle_test_run,
        handle_build,
        handle_typecheck,
        handle_format,
        handle_deps_install,
        handle_job_list,
        handle_job_logs,
        handle_terminal_open,
        handle_editor_open,
    )
    for handler in handlers:
        _attach_dry_run(handler)

    return (
        Verb(
            name="project.dev.start",
            title="Start the project dev server",
            slots={},
            rung=3,
            risk=RiskClass.R1,
            requires=frozenset({"workspace"}),
            support=_ALL_SUPPORT,
            undo=None,
            pack="project",
            handler=handle_dev_start,
        ),
        Verb(
            name="project.dev.stop",
            title="Stop the project dev server",
            slots={"port": SlotSpec(type="int", required=False)},
            rung=3,
            risk=RiskClass.R1,
            requires=frozenset(),
            support=_ALL_SUPPORT,
            undo=None,
            pack="project",
            handler=handle_dev_stop,
        ),
        Verb(
            name="project.dev.restart",
            title="Restart the project dev server",
            slots={},
            rung=3,
            risk=RiskClass.R1,
            requires=frozenset({"workspace"}),
            support=_ALL_SUPPORT,
            undo=None,
            pack="project",
            handler=handle_dev_restart,
        ),
        Verb(
            name="project.test.run",
            title="Run the project test suite",
            slots={
                "scope": SlotSpec(type="str", required=False, default="all"),
                "filter": SlotSpec(type="str", required=False),
                "watch": SlotSpec(type="bool", required=False, default=False),
            },
            rung=3,
            risk=RiskClass.R1,
            requires=frozenset({"workspace"}),
            support=_ALL_SUPPORT,
            undo=None,
            pack="project",
            handler=handle_test_run,
        ),
        Verb(
            name="project.build",
            title="Build the project",
            slots={},
            rung=3,
            risk=RiskClass.R1,
            requires=frozenset({"workspace"}),
            support=_ALL_SUPPORT,
            undo=None,
            pack="project",
            handler=handle_build,
        ),
        Verb(
            name="project.typecheck",
            title="Typecheck the project",
            slots={},
            rung=3,
            risk=RiskClass.R1,
            requires=frozenset({"workspace"}),
            support=_ALL_SUPPORT,
            undo=None,
            pack="project",
            handler=handle_typecheck,
        ),
        Verb(
            name="project.deps.install",
            title="Install project dependencies",
            slots={},
            rung=3,
            risk=RiskClass.R1,
            requires=frozenset({"workspace"}),
            support=_ALL_SUPPORT,
            undo=None,
            pack="project",
            handler=handle_deps_install,
        ),
        Verb(
            name="project.format",
            title="Run the project formatter",
            slots={},
            rung=4,
            risk=RiskClass.R1,
            requires=frozenset({"workspace"}),
            support=_ALL_SUPPORT,
            undo=None,
            pack="project",
            handler=handle_format,
        ),
        Verb(
            name="job.list",
            title="List managed jobs",
            slots={},
            rung=1,
            risk=RiskClass.R0,
            requires=frozenset(),
            support=_ALL_SUPPORT,
            undo=None,
            pack="project",
            handler=handle_job_list,
        ),
        Verb(
            name="job.logs",
            title="Show managed job logs",
            slots={
                "key": SlotSpec(type="str", required=False, default=_DEV_JOB_KEY),
                "tail": SlotSpec(type="int", required=False, default=200),
            },
            rung=1,
            risk=RiskClass.R0,
            requires=frozenset(),
            support=_ALL_SUPPORT,
            undo=None,
            pack="project",
            handler=handle_job_logs,
        ),
        Verb(
            name="app.terminal.open",
            title="Open a terminal at the workspace",
            slots={},
            rung=3,
            risk=RiskClass.R0,
            requires=frozenset({"workspace"}),
            support=_ALL_SUPPORT,
            undo=None,
            pack="project",
            handler=handle_terminal_open,
        ),
        Verb(
            name="editor.open",
            title="Open a path in an editor",
            slots={
                "editor": SlotSpec(type="str", required=False, default="cursor"),
                "path": SlotSpec(type="str", required=False),
                "line": SlotSpec(type="int", required=False),
                "from_error": SlotSpec(type="bool", required=False, default=False),
            },
            rung=3,
            risk=RiskClass.R0,
            requires=frozenset(),
            support=_ALL_SUPPORT,
            undo=None,
            pack="project",
            handler=handle_editor_open,
        ),
    )


def _terminal_argv(platform: PlatformId, cwd: str) -> tuple[str, ...] | None:
    if platform is PlatformId.MACOS:
        return ("open", "-a", "Terminal", cwd)
    if platform is PlatformId.WINDOWS:
        if shutil.which("wt"):
            return ("wt", "-d", cwd)
        return ("cmd", "/c", "start", "cmd", "/k", f"cd /d {cwd}")
    # Linux
    for candidate, args in (
        ("gnome-terminal", ("--working-directory", cwd)),
        ("kgx", ("--working-directory", cwd)),
        ("konsole", ("--workdir", cwd)),
        ("x-terminal-emulator", (f"--working-directory={cwd}",)),
    ):
        if shutil.which(candidate):
            return (candidate, *args)
    return None


def _editor_argv(
    platform: PlatformId,
    editor: str,
    path: Path,
    line: int | None,
) -> tuple[str, ...] | None:
    path_s = os.fspath(path)
    goto = f"{path_s}:{line}" if line is not None else path_s
    key = editor.casefold()
    if key in {"vs code", "vscode", "vs-code"}:
        key = "code"
    cli_names = {
        "cursor": ("cursor", "cursor.cmd"),
        "code": ("code", "code.cmd"),
        "zed": ("zed",),
    }.get(key, (key,))

    for name in cli_names:
        if shutil.which(name):
            if line is not None and key in {"cursor", "code"}:
                return (name, "-g", goto)
            return (name, path_s)

    if platform is PlatformId.MACOS:
        app = {"cursor": "Cursor", "code": "Visual Studio Code", "zed": "Zed"}.get(
            key
        )
        if app:
            return ("open", "-a", app, path_s)
    if platform is PlatformId.WINDOWS:
        return ("cmd", "/c", "start", "", path_s)
    if shutil.which("xdg-open"):
        return ("xdg-open", path_s)
    return None


def register_project_pack(
    registry: Registry,
    *,
    get_supervisor: SupervisorGetter | None = None,
    get_system: SystemGetter | None = None,
    get_terminal: TerminalGetter | None = None,
    get_platform: PlatformGetter | None = None,
    get_cancel: CancelGetter | None = None,
    run_fn: RunFn | None = None,
    popen: Popen | None = None,
    last_runs: list[LastRun] | None = None,
) -> tuple[Pattern, ...]:
    """Register T3.4 verbs onto an existing registry; return grammar patterns."""
    for verb in build_project_verbs(
        get_supervisor=get_supervisor,
        get_system=get_system,
        get_terminal=get_terminal,
        get_platform=get_platform,
        get_cancel=get_cancel,
        run_fn=run_fn,
        popen=popen,
        last_runs=last_runs,
    ):
        registry.register(verb)
    return project_patterns()
