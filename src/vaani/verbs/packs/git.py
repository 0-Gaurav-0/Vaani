"""Git / VCS capability pack (T4.2 / spec §9.1–§9.2).

All argv use ``git -C <repo.root>``. Force push is ``--force-with-lease`` only —
never bare ``--force``.
"""
from __future__ import annotations

import os
import shutil
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from dataclasses import dataclass, field

from vaani.intent.grammar import Pattern, SlotRule
from vaani.intent.normalize import normalize
from vaani.intent.schema import (
    Context,
    Intent,
    PendingAction,
    Result,
    RiskClass,
    SlotSpec,
    Status,
    Support,
    UndoToken,
    Verb,
)
from vaani.intent.slug import (
    branch_names_from_list,
    infer_branch_convention,
    is_plausible_ref,
    slugify_branch,
)
from vaani.platform.protocol import PlatformId
from vaani.verbs.registry import Registry

PACK_NAME = "git"


@dataclass(frozen=True)
class ArgvCommand:
    """Duck-type for ``vaani.exec.runner.Command`` (L3 must not import L5)."""

    argv: tuple[str, ...]
    cwd: Path | None = None
    timeout: float = 20.0
    env_extra: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Completed:
    """Duck-type for ``vaani.exec.runner.Completed``."""

    argv: tuple[str, ...] = ()
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    cancelled: bool = False


def _default_run(cmd: ArgvCommand) -> Completed:
    """Minimal argv runner (no L5 import — layering). Prefer injected ``run_fn``."""
    import subprocess

    try:
        proc = subprocess.run(
            list(cmd.argv),
            cwd=None if cmd.cwd is None else os.fspath(cmd.cwd),
            capture_output=True,
            text=True,
            timeout=cmd.timeout,
            shell=False,
            env=None,
        )
        return Completed(
            argv=cmd.argv,
            returncode=proc.returncode if proc.returncode is not None else -1,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
        )
    except subprocess.TimeoutExpired as exc:
        return Completed(
            argv=cmd.argv,
            returncode=-1,
            stdout=str(exc.stdout or ""),
            stderr=str(exc.stderr or ""),
            timed_out=True,
        )

GIT_VERB_NAMES: frozenset[str] = frozenset(
    {
        "vcs.branch.create",
        "vcs.branch.delete",
        "vcs.checkout",
        "vcs.status",
        "vcs.pull",
        "vcs.commit",
        "vcs.commit.amend",
        "vcs.push",
        "vcs.push.force",
        "vcs.restore",
        "vcs.stash.push",
        "vcs.stash.pop",
        "vcs.diff.show",
    }
)

_ALL_SUPPORT = {
    PlatformId.LINUX: Support.SUPPORTED,
    PlatformId.MACOS: Support.SUPPORTED,
    PlatformId.WINDOWS: Support.SUPPORTED,
}

_PENDING_TTL_S = 20.0
_UNDO_TTL_S = 60.0

RunFn = Callable[..., Completed]
MessageFn = Callable[[Path, RunFn], str]
WhichFn = Callable[[str], str | None]


def git_patterns() -> tuple[Pattern, ...]:
    """Grammar rows for CLI-GIT-* utterances."""
    return (
        Pattern(
            verb="vcs.branch.create",
            any_of=(("create a new branch called", "create branch called"),),
            slots=(
                SlotRule(
                    name="name",
                    regex=r"(?:create(?:\s+a)?(?:\s+new)?\s+branch\s+called)\s+(.+)",
                ),
            ),
            priority=60,
            fixed_slots={"checkout": True},
        ),
        Pattern(
            verb="vcs.branch.create",
            any_of=(("make a branch for the", "make a branch for"),),
            slots=(
                SlotRule(
                    name="name",
                    regex=r"(?:make\s+a\s+branch\s+for(?:\s+the)?)\s+(.+)",
                ),
            ),
            priority=60,
            fixed_slots={"checkout": True},
        ),
        Pattern(
            verb="vcs.branch.create",
            any_of=(("new branch",),),
            slots=(SlotRule(name="name", regex=r"new\s+branch\s+(.+)"),),
            exclude=("switch", "delete"),
            priority=58,
            fixed_slots={"checkout": True},
        ),
        Pattern(
            verb="vcs.checkout",
            any_of=(("switch to", "checkout"),),
            slots=(
                SlotRule(
                    name="ref",
                    regex=r"(?:switch\s+to|checkout)\s+(\S+)",
                ),
            ),
            exclude=("branch", "create", "new"),
            priority=56,
        ),
        Pattern(
            verb="vcs.status",
            any_of=(
                (
                    "git status",
                    "show git status",
                    "what's the git status",
                    "what is the git status",
                    "show the status",
                ),
            ),
            exact=True,
            priority=54,
        ),
        Pattern(
            verb="vcs.pull",
            any_of=(("pull latest", "git pull", "pull from remote"),),
            exact=True,
            priority=54,
        ),
        Pattern(
            verb="vcs.commit",
            any_of=(
                (
                    "commit my changes with a reasonable message",
                    "commit my changes",
                    "git commit",
                    "commit the changes",
                ),
            ),
            exclude=("amend",),
            priority=55,
        ),
        Pattern(
            verb="vcs.commit.amend",
            any_of=(
                (
                    "amend the last commit message",
                    "amend the last commit",
                    "amend commit message",
                ),
            ),
            exact=True,
            priority=57,
        ),
        Pattern(
            verb="vcs.push",
            any_of=(("push this branch", "push the branch", "git push"),),
            exclude=("force",),
            exact=True,
            priority=54,
        ),
        Pattern(
            verb="vcs.push.force",
            any_of=(("force push", "force-push", "git push force"),),
            exact=True,
            priority=58,
        ),
        Pattern(
            verb="vcs.restore",
            any_of=(
                (
                    "discard the changes in this file",
                    "discard changes in this file",
                    "restore this file",
                ),
            ),
            exact=True,
            priority=56,
        ),
        Pattern(
            verb="vcs.stash.push",
            any_of=(("stash my changes", "stash the changes", "git stash"),),
            exclude=("pop",),
            exact=True,
            priority=54,
        ),
        Pattern(
            verb="vcs.stash.pop",
            any_of=(("pop the stash", "stash pop", "git stash pop"),),
            exact=True,
            priority=54,
        ),
        Pattern(
            verb="vcs.diff.show",
            any_of=(
                (
                    "show the diff",
                    "show the git diff",
                    "git diff",
                    "show diff",
                ),
            ),
            exact=True,
            priority=52,
        ),
        Pattern(
            verb="vcs.branch.delete",
            any_of=(("delete the branch", "delete branch"),),
            slots=(
                SlotRule(
                    name="name",
                    regex=r"(?:delete(?:\s+the)?\s+branch)\s+(\S+)",
                ),
            ),
            priority=55,
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


def _repo_root(context: Context, slots: Mapping[str, Any] | None = None) -> Path | None:
    if slots:
        raw = slots.get("repo_root") or slots.get("root")
        if raw:
            return Path(str(raw))
    if context.repo is not None:
        return Path(context.repo.root)
    if context.workspace is not None:
        return Path(context.workspace)
    return None


def _root_arg(root: Path) -> str:
    return os.fspath(root)


def git_argv(root: Path, *args: str) -> tuple[str, ...]:
    """Build ``git -C <root> …`` argv."""
    return ("git", "-C", _root_arg(root), *args)


def materialize_git_argv(
    verb_name: str,
    slots: Mapping[str, Any],
    *,
    context: Context | None = None,
    run_fn: RunFn | None = None,
) -> tuple[str, ...] | None:
    """Dry-run / confirm argv shapes for ``vcs.*`` verbs."""
    root: Path | None = None
    if context is not None:
        root = _repo_root(context, slots)
    if root is None and slots.get("repo_root"):
        root = Path(str(slots["repo_root"]))
    root_s = _root_arg(root) if root is not None else "${repo.root}"
    runner = run_fn or _default_run

    def g(*args: str) -> tuple[str, ...]:
        return ("git", "-C", root_s, *args)

    if verb_name == "vcs.branch.create":
        name = str(slots.get("slug") or slots.get("name") or "branch")
        if root is not None and "slug" not in slots and slots.get("name"):
            name = slugify_branch(normalize(str(slots["name"])))
        return g("switch", "-c", name)
    if verb_name == "vcs.branch.delete":
        name = str(slots.get("name") or "branch")
        return g("branch", "-d", name)
    if verb_name == "vcs.checkout":
        ref = str(slots.get("ref") or slots.get("name") or "HEAD")
        return g("switch", ref)
    if verb_name == "vcs.status":
        return g("status", "--short", "--branch")
    if verb_name == "vcs.pull":
        return g("pull", "--ff-only")
    if verb_name == "vcs.commit":
        message = str(slots.get("message") or "").strip()
        if not message and root is not None:
            message = default_commit_message(root, runner)
        if not message:
            message = "<generated>"
        return g("commit", "-m", message)
    if verb_name == "vcs.commit.amend":
        message = str(slots.get("message") or "")
        if message:
            return g("commit", "--amend", "-m", message)
        return g("commit", "--amend", "-m", "<current>")
    if verb_name == "vcs.push":
        if _truthy(slots.get("set_upstream")):
            remote = str(slots.get("remote") or "origin")
            branch = str(slots.get("branch") or "HEAD")
            return g("push", "-u", remote, branch)
        return g("push")
    if verb_name == "vcs.push.force":
        # Intentionally --force-with-lease only (never bare --force).
        return g("push", "--force-with-lease")
    if verb_name == "vcs.restore":
        path = str(slots.get("path") or slots.get("document_path") or "")
        if not path and context is not None and context.focus is not None:
            doc = context.focus.document_path
            if doc is not None:
                path = os.fspath(doc)
        if not path:
            path = "<focus.document_path>"
        return g("restore", "--", path)
    if verb_name == "vcs.stash.push":
        return g("stash", "push")
    if verb_name == "vcs.stash.pop":
        return g("stash", "pop")
    if verb_name == "vcs.diff.show":
        return g("diff")
    return None


def default_commit_message(root: Path, run_fn: RunFn) -> str:
    """Heuristic commit message from ``git status`` / ``diff --stat`` (no brain yet)."""
    status = run_fn(
        ArgvCommand(argv=git_argv(root, "status", "--porcelain"), timeout=10.0)
    )
    names: list[str] = []
    for ln in (status.stdout or "").splitlines():
        if not ln.rstrip():
            continue
        # porcelain: XY␠PATH — keep leading status columns (do not .strip()).
        body = ln[3:] if len(ln) >= 4 else ln.strip()
        if " -> " in body:
            body = body.split(" -> ", 1)[1]
        names.append(Path(body.strip()).name)
    if not names:
        return "Update repository"
    if len(names) == 1:
        return f"Update {names[0]}"
    if len(names) <= 3:
        return "Update " + ", ".join(names)
    return f"Update {len(names)} files"


def _run(
    run_fn: RunFn,
    root: Path,
    *args: str,
    timeout: float = 20.0,
) -> Completed:
    return run_fn(ArgvCommand(argv=git_argv(root, *args), timeout=timeout))


def _git_ok(completed: Completed) -> bool:
    return (
        completed.returncode == 0
        and not completed.timed_out
        and not completed.cancelled
    )


def _check_ref_format(run_fn: RunFn, root: Path, name: str) -> bool:
    if not is_plausible_ref(name):
        return False
    completed = _run(run_fn, root, "check-ref-format", "--branch", name)
    return _git_ok(completed)


def _list_branches(run_fn: RunFn, root: Path) -> tuple[str, ...]:
    completed = _run(run_fn, root, "branch", "--list", "--format=%(refname:short)")
    if not _git_ok(completed):
        # Fallback for older git without --format.
        completed = _run(run_fn, root, "branch", "--list")
        if not _git_ok(completed):
            return ()
        return branch_names_from_list(completed.stdout or "")
    return tuple(
        ln.strip() for ln in (completed.stdout or "").splitlines() if ln.strip()
    )


def _branch_exists(run_fn: RunFn, root: Path, name: str) -> bool:
    completed = _run(run_fn, root, "show-ref", "--verify", "--quiet", f"refs/heads/{name}")
    return _git_ok(completed)


def _is_dirty(run_fn: RunFn, root: Path) -> bool:
    completed = _run(run_fn, root, "status", "--porcelain")
    if not _git_ok(completed):
        return False
    return bool((completed.stdout or "").strip())


def _dirty_files(run_fn: RunFn, root: Path) -> tuple[str, ...]:
    completed = _run(run_fn, root, "status", "--porcelain")
    if not _git_ok(completed):
        return ()
    out: list[str] = []
    for ln in (completed.stdout or "").splitlines():
        if not ln.rstrip():
            continue
        body = ln[3:] if len(ln) >= 4 else ln.strip()
        if " -> " in body:
            body = body.split(" -> ", 1)[1]
        path = body.strip()
        if path:
            out.append(path)
    return tuple(out)


def _current_branch(run_fn: RunFn, root: Path) -> str | None:
    completed = _run(run_fn, root, "rev-parse", "--abbrev-ref", "HEAD")
    if not _git_ok(completed):
        return None
    text = (completed.stdout or "").strip()
    return text or None


def _has_upstream(run_fn: RunFn, root: Path) -> bool:
    completed = _run(run_fn, root, "rev-parse", "--abbrev-ref", "@{upstream}")
    return _git_ok(completed)


def _commit_is_pushed(run_fn: RunFn, root: Path) -> bool:
    """True when HEAD is reachable from its upstream (amend would rewrite published)."""
    if not _has_upstream(run_fn, root):
        return False
    completed = _run(run_fn, root, "merge-base", "--is-ancestor", "HEAD", "@{upstream}")
    return _git_ok(completed)


def _line_delta(run_fn: RunFn, root: Path, path: Path) -> str:
    rel = os.fspath(path)
    try:
        rel = os.fspath(path.resolve().relative_to(root.resolve()))
    except (OSError, ValueError):
        pass
    completed = _run(run_fn, root, "diff", "--numstat", "--", rel)
    text = (completed.stdout or "").strip()
    if not text:
        # Unstaged empty — try staged.
        completed = _run(run_fn, root, "diff", "--cached", "--numstat", "--", rel)
        text = (completed.stdout or "").strip()
    if not text:
        return "no line changes detected"
    # numstat: added deleted path
    parts = text.split()
    if len(parts) >= 2:
        added, deleted = parts[0], parts[1]
        if added == "-" or deleted == "-":
            return "binary file change"
        return f"+{added}/-{deleted} lines"
    return text


def _refuse_no_repo(context: Context, *, action: str) -> Result:
    ws = context.workspace
    detail = f"Not a git repository (workspace={ws})" if ws else "Not a git repository"
    return Result(
        status=Status.REFUSED,
        summary="No git repository",
        detail=detail,
        evidence=(action,),
        rung=4,
        workspace=context.workspace,
        workspace_source=context.workspace_source,
    )


def _missing_git() -> Result:
    return Result(
        status=Status.UNSUPPORTED,
        summary="git isn't installed",
        detail="git isn't installed",
        rung=4,
    )


def _slug_name(
    spoken: str,
    *,
    run_fn: RunFn,
    root: Path,
) -> str:
    # Normalize spoken separators first ("feature slash …" → "feature/…").
    normalized = normalize(spoken)
    branches = _list_branches(run_fn, root)
    convention = infer_branch_convention(branches)
    return slugify_branch(normalized, convention=convention, branches=branches)


def build_git_verbs(
    *,
    run_fn: RunFn | None = None,
    message_fn: MessageFn | None = None,
    which: WhichFn | None = None,
) -> tuple[Verb, ...]:
    """Build ``vcs.*`` verbs with an injectable argv runner."""
    runner = run_fn or _default_run
    msg_fn = message_fn or default_commit_message
    which_fn = which or shutil.which

    def _ensure_git() -> Result | None:
        if which_fn("git") is None:
            return _missing_git()
        return None

    def handle_branch_create(intent: Intent, context: Context) -> Result:
        missing = _ensure_git()
        if missing is not None:
            return missing
        root = _repo_root(context, intent.slots)
        if root is None:
            return _refuse_no_repo(context, action="vcs.branch.create")
        if context.repo is None and not intent.slots.get("repo_root"):
            probe = _run(runner, root, "rev-parse", "--show-toplevel")
            if not _git_ok(probe):
                return _refuse_no_repo(context, action="vcs.branch.create")

        spoken = str(intent.slots.get("name") or "").strip()
        if not spoken:
            return Result(
                status=Status.FAILED,
                summary="No branch name",
                detail="vcs.branch.create requires name",
                rung=4,
            )
        name = str(
            intent.slots.get("slug") or _slug_name(spoken, run_fn=runner, root=root)
        )
        if not _check_ref_format(runner, root, name):
            return Result(
                status=Status.FAILED,
                summary="Invalid branch name",
                detail=f"{name!r} rejected by git check-ref-format",
                evidence=git_argv(root, "check-ref-format", "--branch", name),
                rung=4,
            )
        if _branch_exists(runner, root, name):
            return Result(
                status=Status.REFUSED,
                summary=f"Branch {name} already exists",
                detail=f"Offer checkout: switch to {name}",
                evidence=git_argv(root, "switch", name),
                rung=4,
            )

        checkout = _truthy(intent.slots.get("checkout", True))
        from_ref = intent.slots.get("from_ref")
        if checkout and _is_dirty(runner, root) and from_ref:
            files = ", ".join(_dirty_files(runner, root)[:8])
            return Result(
                status=Status.REFUSED,
                summary="Dirty tree blocks switch",
                detail=(
                    f"Uncommitted changes ({files}). "
                    "Stash first (vcs.stash.push) then retry."
                ),
                evidence=git_argv(root, "status", "--porcelain"),
                rung=4,
            )

        switch_args: list[str] = ["switch", "-c", name]
        if from_ref:
            switch_args.append(str(from_ref))
        materialized = git_argv(root, *switch_args)

        if "dry_run" in intent.modifiers:
            return Result(
                status=Status.DRY_RUN,
                summary=_display(materialized)[:80],
                detail=f"slug={name}",
                evidence=materialized,
                rung=4,
            )

        # Materialize-then-run: show the slugified name before creation.
        if not _confirmed(intent):
            return Result(
                status=Status.NEEDS_CONFIRM,
                summary=f"Create branch {name}?"[:80],
                detail=f"Slugified from {spoken!r} → {name}",
                evidence=materialized,
                rung=4,
                pending=_pending(
                    verb="vcs.branch.create",
                    slots={
                        "name": spoken,
                        "slug": name,
                        "checkout": checkout,
                        "from_ref": from_ref,
                        "repo_root": _root_arg(root),
                    },
                    materialized=materialized,
                    risk=RiskClass.R1,
                ),
            )

        prev = _current_branch(runner, root)
        completed = _run(runner, root, *switch_args)
        if not _git_ok(completed):
            fallback = ["checkout", "-b", name]
            if from_ref:
                fallback.append(str(from_ref))
            completed = _run(runner, root, *fallback)
            materialized = git_argv(root, *fallback)
        if not _git_ok(completed):
            err = (completed.stderr or completed.stdout or "git failed").strip()
            return Result(
                status=Status.FAILED,
                summary="Could not create branch",
                detail=err[:400],
                evidence=materialized,
                rung=4,
            )
        base = prev or "HEAD"
        undo = UndoToken(
            verb="vcs.branch.create",
            inverse_verb="vcs.branch.delete",
            slots={"name": name, "checkout_ref": prev, "repo_root": _root_arg(root)},
            expires_at=time.time() + _UNDO_TTL_S,
        )
        return Result(
            status=Status.OK,
            summary=f"Created and switched to {name} (from {base})."[:80],
            detail=f"Created and switched to {name} (from {base}).",
            evidence=materialized,
            rung=4,
            undo=undo,
        )

    def handle_branch_delete(intent: Intent, context: Context) -> Result:
        missing = _ensure_git()
        if missing is not None:
            return missing
        root = _repo_root(context, intent.slots)
        if root is None:
            return _refuse_no_repo(context, action="vcs.branch.delete")
        name = str(intent.slots.get("name") or intent.slots.get("slug") or "").strip()
        if not name:
            return Result(
                status=Status.FAILED,
                summary="No branch name",
                detail="vcs.branch.delete requires name",
                rung=4,
            )
        materialized = git_argv(root, "branch", "-d", name)
        if "dry_run" in intent.modifiers:
            return Result(
                status=Status.DRY_RUN,
                summary=_display(materialized)[:80],
                detail=_display(materialized),
                evidence=materialized,
                rung=4,
            )
        if not _confirmed(intent):
            return Result(
                status=Status.NEEDS_CONFIRM,
                summary=f"Delete branch {name}?"[:80],
                detail=_display(materialized),
                evidence=materialized,
                rung=4,
                pending=_pending(
                    verb="vcs.branch.delete",
                    slots={"name": name, "repo_root": _root_arg(root)},
                    materialized=materialized,
                    risk=RiskClass.R3,
                ),
            )
        # Switch away first if deleting current branch.
        current = _current_branch(runner, root)
        checkout_ref = intent.slots.get("checkout_ref") or "main"
        if current == name:
            _run(runner, root, "switch", str(checkout_ref))
        completed = _run(runner, root, "branch", "-d", name)
        if not _git_ok(completed):
            return Result(
                status=Status.FAILED,
                summary="Could not delete branch",
                detail=(completed.stderr or completed.stdout or "").strip()[:400],
                evidence=materialized,
                rung=4,
            )
        return Result(
            status=Status.OK,
            summary=f"Deleted branch {name}"[:80],
            detail=f"Deleted {name}",
            evidence=materialized,
            rung=4,
        )

    def handle_checkout(intent: Intent, context: Context) -> Result:
        missing = _ensure_git()
        if missing is not None:
            return missing
        root = _repo_root(context, intent.slots)
        if root is None:
            return _refuse_no_repo(context, action="vcs.checkout")
        ref = str(intent.slots.get("ref") or intent.slots.get("name") or "").strip()
        if not ref:
            return Result(
                status=Status.FAILED,
                summary="No ref",
                detail="vcs.checkout requires ref",
                rung=4,
            )
        materialized = git_argv(root, "switch", ref)
        if "dry_run" in intent.modifiers:
            return Result(
                status=Status.DRY_RUN,
                summary=_display(materialized)[:80],
                detail=_display(materialized),
                evidence=materialized,
                rung=4,
            )
        dirty = _is_dirty(runner, root)
        if dirty and not _confirmed(intent):
            files = ", ".join(_dirty_files(runner, root)[:8]) or "uncommitted changes"
            return Result(
                status=Status.NEEDS_CONFIRM,
                summary=f"Switch to {ref} with dirty tree?"[:80],
                detail=(
                    f"Dirty tree: {files}. Confirm to try switch, "
                    "or stash first (vcs.stash.push)."
                ),
                evidence=materialized,
                rung=4,
                pending=_pending(
                    verb="vcs.checkout",
                    slots={"ref": ref, "repo_root": _root_arg(root)},
                    materialized=materialized,
                    risk=RiskClass.R2,
                ),
            )
        completed = _run(runner, root, "switch", ref)
        if not _git_ok(completed):
            # fallback checkout
            completed = _run(runner, root, "checkout", ref)
            materialized = git_argv(root, "checkout", ref)
        if not _git_ok(completed):
            return Result(
                status=Status.FAILED,
                summary=f"Could not switch to {ref}"[:80],
                detail=(completed.stderr or completed.stdout or "").strip()[:400],
                evidence=materialized,
                rung=4,
            )
        return Result(
            status=Status.OK,
            summary=f"Switched to {ref}"[:80],
            detail=f"Switched to {ref}",
            evidence=materialized,
            rung=4,
        )

    def handle_status(intent: Intent, context: Context) -> Result:
        missing = _ensure_git()
        if missing is not None:
            return missing
        root = _repo_root(context, intent.slots)
        if root is None:
            return _refuse_no_repo(context, action="vcs.status")
        materialized = git_argv(root, "status", "--short", "--branch")
        if "dry_run" in intent.modifiers:
            return Result(
                status=Status.DRY_RUN,
                summary=_display(materialized)[:80],
                detail=_display(materialized),
                evidence=materialized,
                rung=4,
            )
        completed = _run(runner, root, "status", "--short", "--branch")
        text = (completed.stdout or completed.stderr or "").strip()
        if not _git_ok(completed):
            return Result(
                status=Status.FAILED,
                summary="git status failed",
                detail=text[:400],
                evidence=materialized,
                rung=4,
            )
        summary = text.splitlines()[0] if text else "Clean working tree"
        return Result(
            status=Status.OK,
            summary=summary[:80],
            detail=text or "Clean working tree",
            evidence=materialized,
            rung=4,
        )

    def handle_pull(intent: Intent, context: Context) -> Result:
        missing = _ensure_git()
        if missing is not None:
            return missing
        root = _repo_root(context, intent.slots)
        if root is None:
            return _refuse_no_repo(context, action="vcs.pull")
        materialized = git_argv(root, "pull", "--ff-only")
        if "dry_run" in intent.modifiers:
            return Result(
                status=Status.DRY_RUN,
                summary=_display(materialized)[:80],
                detail=_display(materialized),
                evidence=materialized,
                rung=4,
            )
        completed = _run(runner, root, "pull", "--ff-only")
        if not _git_ok(completed):
            err = (completed.stderr or completed.stdout or "").strip()
            return Result(
                status=Status.FAILED,
                summary="Pull needs merge/rebase",
                detail=(
                    f"--ff-only refused. {err[:300]} "
                    "Resolve with an explicit merge or rebase."
                ),
                evidence=materialized,
                rung=4,
            )
        return Result(
            status=Status.OK,
            summary="Pulled (ff-only)",
            detail=(completed.stdout or "Already up to date.").strip()[:400],
            evidence=materialized,
            rung=4,
        )

    def handle_commit(intent: Intent, context: Context) -> Result:
        missing = _ensure_git()
        if missing is not None:
            return missing
        root = _repo_root(context, intent.slots)
        if root is None:
            return _refuse_no_repo(context, action="vcs.commit")
        if not _is_dirty(runner, root) and not intent.slots.get("message"):
            # Still allow when message pre-set (tests); otherwise nothing to commit.
            staged = _run(runner, root, "diff", "--cached", "--quiet")
            # diff --cached --quiet: 1 means staged changes exist
            has_staged = staged.returncode == 1
            if not has_staged and not _is_dirty(runner, root):
                return Result(
                    status=Status.REFUSED,
                    summary="Nothing to commit",
                    detail="Working tree clean",
                    rung=4,
                )

        message = str(intent.slots.get("message") or "").strip()
        if not message:
            message = msg_fn(root, runner)

        # Stage all tracked/untracked changes for the commit verb.
        materialized = git_argv(root, "commit", "-m", message)
        if "dry_run" in intent.modifiers:
            return Result(
                status=Status.DRY_RUN,
                summary=_display(materialized)[:80],
                detail=f"message={message!r}",
                evidence=materialized,
                rung=4,
            )

        # Flagship: show message verbatim before committing.
        if not _confirmed(intent):
            detail = f"Commit message:\n{message}"
            return Result(
                status=Status.NEEDS_CONFIRM,
                summary=f"Commit: {message}"[:80],
                detail=detail,
                evidence=materialized,
                rung=4,
                pending=_pending(
                    verb="vcs.commit",
                    slots={
                        "message": message,
                        "repo_root": _root_arg(root),
                    },
                    materialized=materialized,
                    risk=RiskClass.R2,
                ),
            )

        _run(runner, root, "add", "-A")
        completed = _run(runner, root, "commit", "-m", message)
        if not _git_ok(completed):
            return Result(
                status=Status.FAILED,
                summary="Commit failed",
                detail=(completed.stderr or completed.stdout or "").strip()[:400],
                evidence=materialized,
                rung=4,
            )
        return Result(
            status=Status.OK,
            summary=f"Committed: {message}"[:80],
            detail=message,
            evidence=materialized,
            rung=4,
        )

    def handle_commit_amend(intent: Intent, context: Context) -> Result:
        missing = _ensure_git()
        if missing is not None:
            return missing
        root = _repo_root(context, intent.slots)
        if root is None:
            return _refuse_no_repo(context, action="vcs.commit.amend")
        if _commit_is_pushed(runner, root) and not _truthy(intent.slots.get("force")):
            return Result(
                status=Status.REFUSED,
                summary="Commit already pushed",
                detail="Refuse amend of a pushed commit unless force=true",
                rung=4,
            )
        message = str(intent.slots.get("message") or "").strip()
        if message:
            materialized = git_argv(root, "commit", "--amend", "-m", message)
            run_args: tuple[str, ...] = ("commit", "--amend", "-m", message)
        else:
            # Keep editor-less: reuse existing subject when no message supplied.
            subject = _run(runner, root, "log", "-1", "--format=%s")
            message = (subject.stdout or "").strip() or "amended"
            materialized = git_argv(root, "commit", "--amend", "-m", message)
            run_args = ("commit", "--amend", "-m", message)

        if "dry_run" in intent.modifiers:
            return Result(
                status=Status.DRY_RUN,
                summary=_display(materialized)[:80],
                detail=f"message={message!r}",
                evidence=materialized,
                rung=4,
            )
        if not _confirmed(intent):
            return Result(
                status=Status.NEEDS_CONFIRM,
                summary=f"Amend: {message}"[:80],
                detail=f"Amend last commit message to:\n{message}",
                evidence=materialized,
                rung=4,
                pending=_pending(
                    verb="vcs.commit.amend",
                    slots={"message": message, "repo_root": _root_arg(root)},
                    materialized=materialized,
                    risk=RiskClass.R2,
                ),
            )
        completed = _run(runner, root, *run_args)
        if not _git_ok(completed):
            return Result(
                status=Status.FAILED,
                summary="Amend failed",
                detail=(completed.stderr or completed.stdout or "").strip()[:400],
                evidence=materialized,
                rung=4,
            )
        return Result(
            status=Status.OK,
            summary=f"Amended: {message}"[:80],
            detail=message,
            evidence=materialized,
            rung=4,
        )

    def handle_push(intent: Intent, context: Context) -> Result:
        missing = _ensure_git()
        if missing is not None:
            return missing
        root = _repo_root(context, intent.slots)
        if root is None:
            return _refuse_no_repo(context, action="vcs.push")
        branch = _current_branch(runner, root) or "HEAD"
        remote = str(intent.slots.get("remote") or "origin")
        if _has_upstream(runner, root):
            materialized = git_argv(root, "push")
            run_args: tuple[str, ...] = ("push",)
            detail_note = f"remote via upstream ({remote})"
        else:
            materialized = git_argv(root, "push", "-u", remote, branch)
            run_args = ("push", "-u", remote, branch)
            detail_note = f"set upstream {remote}/{branch}"
        if "dry_run" in intent.modifiers:
            return Result(
                status=Status.DRY_RUN,
                summary=_display(materialized)[:80],
                detail=detail_note,
                evidence=materialized,
                rung=4,
            )
        completed = _run(runner, root, *run_args, timeout=60.0)
        if not _git_ok(completed):
            return Result(
                status=Status.FAILED,
                summary="Push failed",
                detail=(completed.stderr or completed.stdout or "").strip()[:400],
                evidence=materialized,
                rung=4,
            )
        return Result(
            status=Status.OK,
            summary=f"Pushed to {remote}"[:80],
            detail=detail_note,
            evidence=materialized,
            rung=4,
        )

    def handle_push_force(intent: Intent, context: Context) -> Result:
        missing = _ensure_git()
        if missing is not None:
            return missing
        root = _repo_root(context, intent.slots)
        if root is None:
            return _refuse_no_repo(context, action="vcs.push.force")
        # --force-with-lease ONLY (never bare --force).
        materialized = git_argv(root, "push", "--force-with-lease")
        if "dry_run" in intent.modifiers:
            return Result(
                status=Status.DRY_RUN,
                summary=_display(materialized)[:80],
                detail=_display(materialized),
                evidence=materialized,
                rung=4,
            )
        if not _confirmed(intent):
            return Result(
                status=Status.NEEDS_CONFIRM,
                summary="Force-with-lease push?"[:80],
                detail=_display(materialized),
                evidence=materialized,
                rung=4,
                pending=_pending(
                    verb="vcs.push.force",
                    slots={"repo_root": _root_arg(root)},
                    materialized=materialized,
                    risk=RiskClass.R3,
                ),
            )
        completed = _run(runner, root, "push", "--force-with-lease", timeout=60.0)
        if not _git_ok(completed):
            return Result(
                status=Status.FAILED,
                summary="Force-with-lease push failed",
                detail=(completed.stderr or completed.stdout or "").strip()[:400],
                evidence=materialized,
                rung=4,
            )
        return Result(
            status=Status.OK,
            summary="Force-with-lease push ok",
            detail=_display(materialized),
            evidence=materialized,
            rung=4,
        )

    def handle_restore(intent: Intent, context: Context) -> Result:
        missing = _ensure_git()
        if missing is not None:
            return missing
        root = _repo_root(context, intent.slots)
        if root is None:
            return _refuse_no_repo(context, action="vcs.restore")
        path_raw = intent.slots.get("path") or intent.slots.get("document_path")
        if path_raw is None and context.focus is not None:
            path_raw = context.focus.document_path
        if path_raw is None:
            return Result(
                status=Status.REFUSED,
                summary="No focused file",
                detail="vcs.restore needs focus.document_path",
                rung=4,
            )
        path = Path(str(path_raw))
        delta = _line_delta(runner, root, path)
        try:
            rel = os.fspath(path.resolve().relative_to(root.resolve()))
        except (OSError, ValueError):
            rel = os.fspath(path)
        materialized = git_argv(root, "restore", "--", rel)
        if "dry_run" in intent.modifiers:
            return Result(
                status=Status.DRY_RUN,
                summary=_display(materialized)[:80],
                detail=f"{rel}: {delta}",
                evidence=materialized,
                rung=4,
            )
        if not _confirmed(intent):
            detail = f"Discard changes in {rel} ({delta})?\n{_display(materialized)}"
            return Result(
                status=Status.NEEDS_CONFIRM,
                summary=f"Restore {rel} ({delta})"[:80],
                detail=detail,
                evidence=materialized,
                rung=4,
                pending=_pending(
                    verb="vcs.restore",
                    slots={"path": rel, "repo_root": _root_arg(root)},
                    materialized=materialized,
                    risk=RiskClass.R3,
                ),
            )
        completed = _run(runner, root, "restore", "--", rel)
        if not _git_ok(completed):
            return Result(
                status=Status.FAILED,
                summary="Restore failed",
                detail=(completed.stderr or completed.stdout or "").strip()[:400],
                evidence=materialized,
                rung=4,
            )
        return Result(
            status=Status.OK,
            summary=f"Restored {rel}"[:80],
            detail=f"Restored {rel} ({delta})",
            evidence=materialized,
            rung=4,
        )

    def handle_stash_push(intent: Intent, context: Context) -> Result:
        missing = _ensure_git()
        if missing is not None:
            return missing
        root = _repo_root(context, intent.slots)
        if root is None:
            return _refuse_no_repo(context, action="vcs.stash.push")
        materialized = git_argv(root, "stash", "push")
        if "dry_run" in intent.modifiers:
            return Result(
                status=Status.DRY_RUN,
                summary=_display(materialized)[:80],
                detail=_display(materialized),
                evidence=materialized,
                rung=4,
            )
        completed = _run(runner, root, "stash", "push")
        if not _git_ok(completed):
            return Result(
                status=Status.FAILED,
                summary="Stash failed",
                detail=(completed.stderr or completed.stdout or "").strip()[:400],
                evidence=materialized,
                rung=4,
            )
        undo = UndoToken(
            verb="vcs.stash.push",
            inverse_verb="vcs.stash.pop",
            slots={"repo_root": _root_arg(root)},
            expires_at=time.time() + _UNDO_TTL_S,
        )
        return Result(
            status=Status.OK,
            summary="Stashed changes",
            detail=(completed.stdout or "stash push").strip()[:400],
            evidence=materialized,
            rung=4,
            undo=undo,
        )

    def handle_stash_pop(intent: Intent, context: Context) -> Result:
        missing = _ensure_git()
        if missing is not None:
            return missing
        root = _repo_root(context, intent.slots)
        if root is None:
            return _refuse_no_repo(context, action="vcs.stash.pop")
        materialized = git_argv(root, "stash", "pop")
        if "dry_run" in intent.modifiers:
            return Result(
                status=Status.DRY_RUN,
                summary=_display(materialized)[:80],
                detail=_display(materialized),
                evidence=materialized,
                rung=4,
            )
        completed = _run(runner, root, "stash", "pop")
        if not _git_ok(completed):
            return Result(
                status=Status.FAILED,
                summary="Stash pop failed",
                detail=(completed.stderr or completed.stdout or "").strip()[:400],
                evidence=materialized,
                rung=4,
            )
        undo = UndoToken(
            verb="vcs.stash.pop",
            inverse_verb="vcs.stash.push",
            slots={"repo_root": _root_arg(root)},
            expires_at=time.time() + _UNDO_TTL_S,
        )
        return Result(
            status=Status.OK,
            summary="Popped stash",
            detail=(completed.stdout or "stash pop").strip()[:400],
            evidence=materialized,
            rung=4,
            undo=undo,
        )

    def handle_diff_show(intent: Intent, context: Context) -> Result:
        missing = _ensure_git()
        if missing is not None:
            return missing
        root = _repo_root(context, intent.slots)
        if root is None:
            return _refuse_no_repo(context, action="vcs.diff.show")
        materialized = git_argv(root, "diff")
        if "dry_run" in intent.modifiers:
            return Result(
                status=Status.DRY_RUN,
                summary=_display(materialized)[:80],
                detail=_display(materialized),
                evidence=materialized,
                rung=4,
            )
        # Prefer opening via git difftool when available; else return the diff text.
        tool = _run(runner, root, "difftool", "--no-prompt")
        if _git_ok(tool) or tool.returncode in {0, 1}:
            # difftool may return 1; treat as launched when no hard error.
            if "unknown option" not in (tool.stderr or "").casefold():
                return Result(
                    status=Status.OK,
                    summary="Opened diff",
                    detail="Launched git difftool",
                    evidence=git_argv(root, "difftool", "--no-prompt"),
                    rung=4,
                )
        completed = _run(runner, root, "diff")
        text = (completed.stdout or "").strip()
        summary = "No changes" if not text else f"Diff ({len(text.splitlines())} lines)"
        return Result(
            status=Status.OK,
            summary=summary[:80],
            detail=text[:2000] if text else "Working tree clean",
            evidence=materialized,
            rung=4,
        )

    return (
        Verb(
            name="vcs.branch.create",
            title="Create a git branch",
            slots={
                "name": SlotSpec(type="str", required=True),
                "from_ref": SlotSpec(type="str", required=False),
                "checkout": SlotSpec(type="bool", required=False, default=True),
            },
            rung=4,
            risk=RiskClass.R1,
            requires=frozenset({"repo"}),
            support=_ALL_SUPPORT,
            undo="vcs.branch.delete",
            pack=PACK_NAME,
            handler=handle_branch_create,
        ),
        Verb(
            name="vcs.branch.delete",
            title="Delete a git branch",
            slots={"name": SlotSpec(type="str", required=True)},
            rung=4,
            risk=RiskClass.R3,
            requires=frozenset({"repo"}),
            support=_ALL_SUPPORT,
            undo=None,
            pack=PACK_NAME,
            handler=handle_branch_delete,
        ),
        Verb(
            name="vcs.checkout",
            title="Switch git branch",
            slots={"ref": SlotSpec(type="str", required=True)},
            rung=4,
            risk=RiskClass.R1,
            requires=frozenset({"repo"}),
            support=_ALL_SUPPORT,
            undo=None,
            pack=PACK_NAME,
            handler=handle_checkout,
        ),
        Verb(
            name="vcs.status",
            title="Show git status",
            slots={},
            rung=4,
            risk=RiskClass.R0,
            requires=frozenset({"repo"}),
            support=_ALL_SUPPORT,
            undo=None,
            pack=PACK_NAME,
            handler=handle_status,
        ),
        Verb(
            name="vcs.pull",
            title="Pull with --ff-only",
            slots={},
            rung=4,
            risk=RiskClass.R1,
            requires=frozenset({"repo"}),
            support=_ALL_SUPPORT,
            undo=None,
            pack=PACK_NAME,
            handler=handle_pull,
        ),
        Verb(
            name="vcs.commit",
            title="Commit with generated message",
            slots={"message": SlotSpec(type="str", required=False)},
            rung=4,
            risk=RiskClass.R2,
            requires=frozenset({"repo"}),
            support=_ALL_SUPPORT,
            undo=None,
            pack=PACK_NAME,
            handler=handle_commit,
        ),
        Verb(
            name="vcs.commit.amend",
            title="Amend last commit message",
            slots={
                "message": SlotSpec(type="str", required=False),
                "force": SlotSpec(type="bool", required=False, default=False),
            },
            rung=4,
            risk=RiskClass.R2,
            requires=frozenset({"repo"}),
            support=_ALL_SUPPORT,
            undo=None,
            pack=PACK_NAME,
            handler=handle_commit_amend,
        ),
        Verb(
            name="vcs.push",
            title="Push current branch",
            slots={},
            rung=4,
            risk=RiskClass.R1,
            requires=frozenset({"repo"}),
            support=_ALL_SUPPORT,
            undo=None,
            pack=PACK_NAME,
            handler=handle_push,
        ),
        Verb(
            name="vcs.push.force",
            title="Force-with-lease push",
            slots={},
            rung=4,
            risk=RiskClass.R3,
            requires=frozenset({"repo"}),
            support=_ALL_SUPPORT,
            undo=None,
            pack=PACK_NAME,
            handler=handle_push_force,
        ),
        Verb(
            name="vcs.restore",
            title="Discard file changes",
            slots={"path": SlotSpec(type="str", required=False)},
            rung=4,
            risk=RiskClass.R3,
            requires=frozenset({"repo", "focus"}),
            support=_ALL_SUPPORT,
            undo=None,
            pack=PACK_NAME,
            handler=handle_restore,
        ),
        Verb(
            name="vcs.stash.push",
            title="Stash changes",
            slots={},
            rung=4,
            risk=RiskClass.R1,
            requires=frozenset({"repo"}),
            support=_ALL_SUPPORT,
            undo="vcs.stash.pop",
            pack=PACK_NAME,
            handler=handle_stash_push,
        ),
        Verb(
            name="vcs.stash.pop",
            title="Pop stash",
            slots={},
            rung=4,
            risk=RiskClass.R1,
            requires=frozenset({"repo"}),
            support=_ALL_SUPPORT,
            undo="vcs.stash.push",
            pack=PACK_NAME,
            handler=handle_stash_pop,
        ),
        Verb(
            name="vcs.diff.show",
            title="Show git diff",
            slots={},
            rung=4,
            risk=RiskClass.R0,
            requires=frozenset({"repo"}),
            support=_ALL_SUPPORT,
            undo=None,
            pack=PACK_NAME,
            handler=handle_diff_show,
        ),
    )


def register_git_pack(
    registry: Registry,
    *,
    run_fn: RunFn | None = None,
    message_fn: MessageFn | None = None,
    which: WhichFn | None = None,
) -> tuple[Pattern, ...]:
    """Register ``vcs.*`` verbs; return grammar patterns."""
    for verb in build_git_verbs(run_fn=run_fn, message_fn=message_fn, which=which):
        registry.register(verb)
    return git_patterns()
