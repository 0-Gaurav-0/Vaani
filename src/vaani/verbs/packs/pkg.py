"""Package-manager pack (T4.4): ``pkg.add`` / ``remove`` / ``lock`` / ``script.run`` / ``reinstall``.

Manager is always taken from the lockfile / ``ProjectProfile``, never from speech.
"""
from __future__ import annotations

import json
import os
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

PACK_NAME = "pkg"

PKG_VERB_NAMES: frozenset[str] = frozenset(
    {
        "pkg.add",
        "pkg.remove",
        "pkg.lock",
        "pkg.script.run",
        "pkg.reinstall",
    }
)

_ALL_SUPPORT = {
    PlatformId.LINUX: Support.SUPPORTED,
    PlatformId.MACOS: Support.SUPPORTED,
    PlatformId.WINDOWS: Support.SUPPORTED,
}

_PENDING_TTL_S = 20.0
_ONESHOT_TIMEOUT_S = 600.0

# Default directory wiped by ``pkg.reinstall`` (inside workspace).
_REINSTALL_DIRS: dict[str, str] = {
    "npm": "node_modules",
    "pnpm": "node_modules",
    "yarn": "node_modules",
    "bun": "node_modules",
    "uv": ".venv",
    "poetry": ".venv",
    "pipenv": ".venv",
    "pip": ".venv",
    "hatch": ".venv",
}

_ADD_ARGV: dict[str, tuple[str, ...]] = {
    "npm": ("npm", "install"),
    "pnpm": ("pnpm", "add"),
    "yarn": ("yarn", "add"),
    "bun": ("bun", "add"),
    "uv": ("uv", "add"),
    "poetry": ("poetry", "add"),
    "pipenv": ("pipenv", "install"),
    "pip": ("pip", "install"),
}

_REMOVE_ARGV: dict[str, tuple[str, ...]] = {
    "npm": ("npm", "uninstall"),
    "pnpm": ("pnpm", "remove"),
    "yarn": ("yarn", "remove"),
    "bun": ("bun", "remove"),
    "uv": ("uv", "remove"),
    "poetry": ("poetry", "remove"),
    "pipenv": ("pipenv", "uninstall"),
    "pip": ("pip", "uninstall"),
}

_LOCK_ARGV: dict[str, tuple[str, ...]] = {
    "npm": ("npm", "install", "--package-lock-only"),
    "pnpm": ("pnpm", "install", "--lockfile-only"),
    "yarn": ("yarn", "install", "--mode=update-lockfile"),
    "bun": ("bun", "install", "--lockfile-only"),
    "uv": ("uv", "lock"),
    "poetry": ("poetry", "lock"),
    "pipenv": ("pipenv", "lock"),
}

_INSTALL_ARGV: dict[str, tuple[str, ...]] = {
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

RunFn = Callable[..., Any]
WhichFn = Callable[[str], str | None]
RmtreeFn = Callable[[Path], None]


@dataclass(frozen=True)
class ArgvCommand:
    """Duck-typed argv carrier for injected runner (no L5 import)."""

    argv: tuple[str, ...]
    cwd: Path | None = None
    timeout: float = 20.0
    env_extra: Mapping[str, str] = field(default_factory=dict)


def pkg_patterns() -> tuple[Pattern, ...]:
    """Grammar rows for CLI-PKG-01..04."""
    return (
        Pattern(
            verb="pkg.add",
            any_of=(("install",),),
            slots=(
                SlotRule(
                    name="package",
                    regex=r"install\s+(.+?)(?:\s+to\s+(?:the\s+)?project)?$",
                ),
            ),
            exclude=(
                "dependencies",
                "deps",
                "the dependencies",
                "node_modules",
                "and reinstall",
            ),
            priority=58,
        ),
        Pattern(
            verb="pkg.add",
            any_of=(("add",),),
            slots=(
                SlotRule(
                    name="package",
                    regex=r"add\s+(.+?)(?:\s+to\s+(?:the\s+)?project)?$",
                ),
            ),
            exclude=("dependencies", "deps"),
            priority=58,
        ),
        Pattern(
            verb="pkg.remove",
            any_of=(("remove", "uninstall"),),
            slots=(
                SlotRule(
                    name="package",
                    regex=r"(?:remove|uninstall)\s+(.+?)(?:\s+from\s+(?:the\s+)?project)?$",
                ),
            ),
            exclude=("node_modules", "and reinstall", "dependencies", "deps"),
            priority=58,
        ),
        Pattern(
            verb="pkg.lock",
            any_of=(
                (
                    "update the lockfile",
                    "update lockfile",
                    "refresh the lockfile",
                    "refresh lockfile",
                ),
            ),
            exact=True,
            priority=56,
        ),
        Pattern(
            verb="pkg.script.run",
            any_of=(("npm run", "pnpm run", "yarn run", "bun run", "run npm run", "run pnpm run"),),
            slots=(
                SlotRule(
                    name="script",
                    regex=r"(?:run\s+)?(?:npm|pnpm|yarn|bun)\s+run\s+(\S+)",
                ),
            ),
            priority=60,
        ),
        Pattern(
            verb="pkg.reinstall",
            any_of=(
                (
                    "remove node_modules and reinstall",
                    "delete node_modules and reinstall",
                    "wipe node_modules and reinstall",
                    "reinstall node_modules",
                    "reinstall the dependencies",
                ),
            ),
            exact=True,
            priority=60,
            fixed_slots={"path": "node_modules"},
        ),
    )


def path_inside_workspace(workspace: Path, candidate: str | Path) -> Path | None:
    """Return resolved path if it stays inside ``workspace`` after symlink resolution.

    Rejects ``../`` escapes and symlink escapes that resolve outside the workspace.
    """
    try:
        ws = workspace.expanduser().resolve()
    except OSError:
        return None
    raw = Path(candidate)
    target = raw if raw.is_absolute() else (ws / raw)
    try:
        resolved = target.resolve()
    except OSError:
        return None
    try:
        if not resolved.is_relative_to(ws):
            return None
    except (ValueError, OSError):
        return None
    return resolved


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


def _refuse_miss(miss: ProfileMiss, *, context: Context, field: str) -> Result:
    msg = miss.refusal_message()
    checked = ", ".join(miss.checked) if miss.checked else "(no workspace)"
    return _result(
        status=Status.REFUSED,
        summary=f"No project profile ({field})",
        detail=f"No {field} tooling detected. {msg} Checked: {checked}.",
        evidence=tuple(miss.checked),
        context=context,
    )


def _clean_package_name(raw: Any) -> str:
    text = str(raw or "").strip()
    # Strip trailing "to the project" residue if regex left it.
    for suffix in (" to the project", " to project", " from the project", " from project"):
        if text.casefold().endswith(suffix):
            text = text[: -len(suffix)].strip()
    return text


def _package_scripts(root: Path) -> dict[str, str]:
    path = root / "package.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    scripts = data.get("scripts")
    if not isinstance(scripts, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in scripts.items():
        if isinstance(key, str) and isinstance(value, str):
            out[key] = value
    return out


def _script_argv(manager: str, script: str) -> tuple[str, ...]:
    mgr = manager if manager in {"npm", "pnpm", "yarn", "bun"} else "npm"
    if mgr == "yarn":
        return ("yarn", script)
    if mgr == "bun":
        return ("bun", "run", script)
    return (mgr, "run", script)


def _default_rmtree(path: Path) -> None:
    shutil.rmtree(path)


def build_pkg_verbs(
    *,
    run_fn: RunFn | None = None,
    which: WhichFn | None = None,
    rmtree: RmtreeFn | None = None,
) -> tuple[Verb, ...]:
    """Build T4.4 package verbs with injectable runner / which / rmtree seams."""

    which_fn: WhichFn = which or shutil.which
    remove_tree: RmtreeFn = rmtree or _default_rmtree

    def _require_manager(
        context: Context,
    ) -> tuple[ProjectProfile, str] | Result:
        root = _resolve_workspace_root(context)
        if root is None:
            return _result(
                status=Status.REFUSED,
                summary="No workspace",
                detail="pkg verbs require a workspace",
                context=context,
            )
        profile = _profile_for(context)
        if isinstance(profile, ProfileMiss):
            return _refuse_miss(profile, context=context, field="package manager")
        manager = profile.manager
        if not manager:
            return _refuse_miss(
                ProfileMiss(root=profile.root),
                context=context,
                field="package manager",
            )
        if which_fn(manager) is None:
            reason = missing_binary_reason(manager)
            return _result(
                status=Status.UNSUPPORTED,
                summary=reason,
                detail=reason,
                evidence=(manager,),
                context=context,
            )
        return profile, manager

    def _run(
        intent: Intent,
        context: Context,
        *,
        argv: tuple[str, ...],
        root: Path,
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
                summary="Package runner unavailable",
                detail="pkg pack requires an injected run_fn",
                evidence=argv,
                context=context,
            )
        completed = run_fn(
            ArgvCommand(argv=argv, cwd=root, timeout=_ONESHOT_TIMEOUT_S)
        )
        code = int(getattr(completed, "returncode", 1))
        out = str(getattr(completed, "stdout", "") or "")
        err = str(getattr(completed, "stderr", "") or "")
        log_text = "\n".join(part for part in (out, err) if part)
        if getattr(completed, "cancelled", False):
            return _result(
                status=Status.FAILED,
                summary="Package command cancelled",
                detail="Cancelled mid-run",
                evidence=argv + (f"exit={code}",),
                context=context,
            )
        if getattr(completed, "timed_out", False):
            return _result(
                status=Status.FAILED,
                summary="Package command timed out",
                detail=f"Timed out after {_ONESHOT_TIMEOUT_S:.0f}s",
                evidence=argv + ("timed_out",),
                context=context,
            )
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
            summary=f"Package command failed (exit {code})",
            detail=log_text[-4000:] if log_text else f"exit={code}",
            evidence=argv + (f"exit={code}",),
            context=context,
        )

    def handle_add(intent: Intent, context: Context) -> Result:
        resolved = _require_manager(context)
        if isinstance(resolved, Result):
            return resolved
        profile, manager = resolved
        package = _clean_package_name(intent.slots.get("package"))
        if not package:
            return _result(
                status=Status.FAILED,
                summary="No package name",
                detail="pkg.add requires a package slot (name as heard)",
                context=context,
            )
        prefix = _ADD_ARGV.get(manager)
        if prefix is None:
            return _result(
                status=Status.REFUSED,
                summary=f"Unsupported manager {manager}",
                detail=f"No add argv for manager {manager!r}",
                evidence=(manager,),
                context=context,
            )
        # Spoken package name is shown verbatim — typosquat risk → R2.
        materialized = (*prefix, package)
        if "dry_run" in intent.modifiers:
            return _result(
                status=Status.DRY_RUN,
                summary=_display(materialized)[:80],
                detail=f"Add {package!r} via {manager} (from lockfile)",
                evidence=materialized,
                context=context,
            )
        if not _confirmed(intent):
            summary = f"Add package {package!r} with {manager}?"
            return _result(
                status=Status.NEEDS_CONFIRM,
                summary=summary[:80],
                detail=summary + f" argv: {_display(materialized)}",
                evidence=materialized,
                pending=_pending(
                    verb="pkg.add",
                    slots={"package": package},
                    materialized=materialized,
                    risk=RiskClass.R2,
                ),
                context=context,
            )
        return _run(intent, context, argv=materialized, root=Path(profile.root))

    def handle_remove(intent: Intent, context: Context) -> Result:
        resolved = _require_manager(context)
        if isinstance(resolved, Result):
            return resolved
        profile, manager = resolved
        package = _clean_package_name(intent.slots.get("package"))
        if not package:
            return _result(
                status=Status.FAILED,
                summary="No package name",
                detail="pkg.remove requires a package slot",
                context=context,
            )
        prefix = _REMOVE_ARGV.get(manager)
        if prefix is None:
            return _result(
                status=Status.REFUSED,
                summary=f"Unsupported manager {manager}",
                detail=f"No remove argv for manager {manager!r}",
                evidence=(manager,),
                context=context,
            )
        materialized = (*prefix, package)
        if "dry_run" in intent.modifiers:
            return _result(
                status=Status.DRY_RUN,
                summary=_display(materialized)[:80],
                detail=f"Remove {package!r} via {manager}",
                evidence=materialized,
                context=context,
            )
        if not _confirmed(intent):
            summary = f"Remove package {package!r} with {manager}?"
            return _result(
                status=Status.NEEDS_CONFIRM,
                summary=summary[:80],
                detail=summary + f" argv: {_display(materialized)}",
                evidence=materialized,
                pending=_pending(
                    verb="pkg.remove",
                    slots={"package": package},
                    materialized=materialized,
                    risk=RiskClass.R2,
                ),
                context=context,
            )
        return _run(intent, context, argv=materialized, root=Path(profile.root))

    def handle_lock(intent: Intent, context: Context) -> Result:
        resolved = _require_manager(context)
        if isinstance(resolved, Result):
            return resolved
        profile, manager = resolved
        argv = _LOCK_ARGV.get(manager)
        if argv is None:
            return _result(
                status=Status.REFUSED,
                summary=f"Unsupported manager {manager}",
                detail=f"No lockfile update argv for manager {manager!r}",
                evidence=(manager,),
                context=context,
            )
        return _run(intent, context, argv=argv, root=Path(profile.root))

    def handle_script_run(intent: Intent, context: Context) -> Result:
        resolved = _require_manager(context)
        if isinstance(resolved, Result):
            return resolved
        profile, manager = resolved
        script = str(intent.slots.get("script") or "").strip()
        if not script:
            return _result(
                status=Status.FAILED,
                summary="No script name",
                detail="pkg.script.run requires a script slot",
                context=context,
            )
        scripts = _package_scripts(Path(profile.root))
        if scripts and script not in scripts:
            known = ", ".join(sorted(scripts)) or "(none)"
            return _result(
                status=Status.REFUSED,
                summary=f"Unknown script {script!r}",
                detail=f"Scripts in package.json: {known}",
                evidence=tuple(sorted(scripts)),
                context=context,
            )
        argv = _script_argv(manager, script)
        return _run(intent, context, argv=argv, root=Path(profile.root))

    def handle_reinstall(intent: Intent, context: Context) -> Result:
        resolved = _require_manager(context)
        if isinstance(resolved, Result):
            return resolved
        profile, manager = resolved
        root = Path(profile.root)
        rel = str(intent.slots.get("path") or _REINSTALL_DIRS.get(manager) or "node_modules")
        target = path_inside_workspace(root, rel)
        if target is None:
            return _result(
                status=Status.REFUSED,
                summary="Path escapes workspace",
                detail=(
                    f"pkg.reinstall refuses path {rel!r}: resolved location "
                    "must stay inside the workspace (no ../ or symlink escape)."
                ),
                evidence=(rel,),
                context=context,
            )
        install = _INSTALL_ARGV.get(manager)
        if install is None:
            return _result(
                status=Status.REFUSED,
                summary=f"Unsupported manager {manager}",
                detail=f"No install argv for manager {manager!r}",
                evidence=(manager,),
                context=context,
            )
        # Materialize as delete + install for confirm / dry-run evidence.
        materialized = ("rm", "-rf", os.fspath(target), "&&", *install)
        if "dry_run" in intent.modifiers:
            return _result(
                status=Status.DRY_RUN,
                summary=_display(materialized)[:80],
                detail=(
                    f"Delete {target} then {_display(install)} "
                    f"(manager={manager} from lockfile)"
                ),
                evidence=materialized,
                context=context,
            )
        if not _confirmed(intent):
            summary = f"Delete {target.name} and reinstall with {manager}?"
            return _result(
                status=Status.NEEDS_CONFIRM,
                summary=summary[:80],
                detail=f"{summary} path={target} argv={_display(install)}",
                evidence=materialized,
                pending=_pending(
                    verb="pkg.reinstall",
                    slots={"path": rel},
                    materialized=materialized,
                    risk=RiskClass.R3,
                ),
                context=context,
            )
        # Re-check after confirm in case the tree changed (symlink swap).
        target = path_inside_workspace(root, rel)
        if target is None:
            return _result(
                status=Status.REFUSED,
                summary="Path escapes workspace",
                detail=f"pkg.reinstall refuses path {rel!r} after confirm.",
                evidence=(rel,),
                context=context,
            )
        if target.exists():
            try:
                remove_tree(target)
            except OSError as exc:
                return _result(
                    status=Status.FAILED,
                    summary="Could not delete path",
                    detail=str(exc),
                    evidence=(os.fspath(target),),
                    context=context,
                )
        return _run(intent, context, argv=install, root=root)

    return (
        Verb(
            name="pkg.add",
            title="Add a package",
            slots={"package": SlotSpec(type="str", required=True)},
            rung=4,
            risk=RiskClass.R2,
            requires=frozenset({"workspace"}),
            support=_ALL_SUPPORT,
            undo=None,
            pack=PACK_NAME,
            handler=handle_add,
        ),
        Verb(
            name="pkg.remove",
            title="Remove a package",
            slots={"package": SlotSpec(type="str", required=True)},
            rung=4,
            risk=RiskClass.R2,
            requires=frozenset({"workspace"}),
            support=_ALL_SUPPORT,
            undo=None,
            pack=PACK_NAME,
            handler=handle_remove,
        ),
        Verb(
            name="pkg.lock",
            title="Update the lockfile",
            slots={},
            rung=4,
            risk=RiskClass.R1,
            requires=frozenset({"workspace"}),
            support=_ALL_SUPPORT,
            undo=None,
            pack=PACK_NAME,
            handler=handle_lock,
        ),
        Verb(
            name="pkg.script.run",
            title="Run a package script",
            slots={"script": SlotSpec(type="str", required=True)},
            rung=4,
            risk=RiskClass.R1,
            requires=frozenset({"workspace"}),
            support=_ALL_SUPPORT,
            undo=None,
            pack=PACK_NAME,
            handler=handle_script_run,
        ),
        Verb(
            name="pkg.reinstall",
            title="Delete install dir and reinstall",
            slots={"path": SlotSpec(type="str", required=False, default="node_modules")},
            rung=4,
            risk=RiskClass.R3,
            requires=frozenset({"workspace"}),
            support=_ALL_SUPPORT,
            undo=None,
            pack=PACK_NAME,
            handler=handle_reinstall,
        ),
    )


def register_pkg_pack(
    registry: Registry,
    *,
    run_fn: RunFn | None = None,
    which: WhichFn | None = None,
    rmtree: RmtreeFn | None = None,
) -> tuple[Pattern, ...]:
    """Register ``pkg.*`` verbs; return grammar patterns."""
    for verb in build_pkg_verbs(run_fn=run_fn, which=which, rmtree=rmtree):
        registry.register(verb)
    return pkg_patterns()
