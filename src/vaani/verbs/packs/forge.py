"""Forge / ``gh`` pack (T4.3 / spec §9.2 CLI-GH / CLI-AUTH).

Prefer ``gh`` argv tuples (never ``shell=True``). Missing ``gh`` and missing
auth surface as clear degrade reasons — never rung-6 escalation.
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

from vaani.intent.grammar import Pattern, SlotRule
from vaani.intent.schema import (
    Context,
    DisambiguationOption,
    DisambiguationPrompt,
    Intent,
    PendingAction,
    Result,
    RiskClass,
    SlotSpec,
    Status,
    Support,
    Verb,
)
from vaani.platform.protocol import PlatformId
from vaani.verbs.packs.registry import (
    PackPrerequisiteError,
    PackRegistry,
    missing_binary_reason,
)
from vaani.verbs.registry import Registry

# Keep in sync with ``policy.disambiguate.MAX_OPTIONS`` (L3 must not import L4).
_MAX_DISAMBIG_OPTIONS = 3

PACK_NAME = "forge"

FORGE_VERB_NAMES: frozenset[str] = frozenset(
    {
        "forge.pr.create",
        "forge.pr.open",
        "forge.pr.list",
        "forge.pr.checkout",
        "forge.pr.merge",
        "forge.auth.login",
    }
)

_ALL_SUPPORT = {
    PlatformId.LINUX: Support.SUPPORTED,
    PlatformId.MACOS: Support.SUPPORTED,
    PlatformId.WINDOWS: Support.SUPPORTED,
}

_PENDING_TTL_S = 20.0
_GH_BIN = "gh"
_AUTH_DEGRADED = "gh isn't logged in"
_DEFAULT_STRATEGY = "merge"
_STRATEGY_FLAGS = {
    "merge": "--merge",
    "squash": "--squash",
    "rebase": "--rebase",
}

RunFn = Callable[..., Any]
WhichFn = Callable[[str], str | None]
# Optional T4.5 seam: ``(candidates, prompt) -> chosen key | None``.
DisambiguateFn = Callable[[Sequence[str], str], str | None]


@dataclass(frozen=True)
class ArgvCommand:
    """Duck-typed argv carrier for injected runner (no L5 import)."""

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


def forge_patterns() -> tuple[Pattern, ...]:
    """Grammar rows for CLI-GH-01..04 and CLI-AUTH-01."""
    return (
        Pattern(
            verb="forge.pr.create",
            any_of=(
                (
                    "create a pull request",
                    "create pull request",
                    "create a pr",
                    "create pr",
                ),
            ),
            exact=True,
            priority=58,
        ),
        Pattern(
            verb="forge.pr.open",
            any_of=(
                (
                    "open the pr i was working on",
                    "open the pull request i was working on",
                    "open my pr",
                    "open my pull request",
                ),
            ),
            exact=True,
            priority=58,
        ),
        Pattern(
            verb="forge.pr.list",
            any_of=(
                (
                    "list my open prs",
                    "list my open pull requests",
                    "list open prs",
                    "list open pull requests",
                    "show my open prs",
                ),
            ),
            exact=True,
            priority=56,
        ),
        Pattern(
            verb="forge.pr.checkout",
            any_of=(
                (
                    "check out the pr branch for pull request",
                    "checkout the pr branch for pull request",
                    "check out pull request",
                    "checkout pull request",
                    "check out pr",
                    "checkout pr",
                ),
            ),
            slots=(
                SlotRule(
                    name="number",
                    regex=r"(?:pull\s+request|pr)\s+(\d+)",
                ),
            ),
            priority=58,
        ),
        Pattern(
            verb="forge.pr.merge",
            any_of=(
                (
                    "merge the pull request",
                    "merge the pr",
                    "merge pull request",
                    "merge pr",
                ),
            ),
            exact=True,
            priority=58,
        ),
        Pattern(
            verb="forge.auth.login",
            any_of=(
                (
                    "login to gh",
                    "log in to gh",
                    "gh auth login",
                    "login to github cli",
                ),
            ),
            exact=True,
            priority=58,
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


def _ok(completed: Any) -> bool:
    return (
        int(getattr(completed, "returncode", 1)) == 0
        and not bool(getattr(completed, "timed_out", False))
        and not bool(getattr(completed, "cancelled", False))
    )


def _stdout(completed: Any) -> str:
    return str(getattr(completed, "stdout", "") or "")


def _stderr(completed: Any) -> str:
    return str(getattr(completed, "stderr", "") or "")


def _parse_pr_number(raw: Any) -> int | None:
    if raw is None:
        return None
    if isinstance(raw, int):
        return raw if raw > 0 else None
    text = str(raw).strip()
    if not text:
        return None
    if text.isdigit():
        value = int(text)
        return value if value > 0 else None
    return None


def _normalize_strategy(raw: Any) -> str:
    name = str(raw or _DEFAULT_STRATEGY).strip().casefold()
    if name not in _STRATEGY_FLAGS:
        return _DEFAULT_STRATEGY
    return name


def _options_from_prs(
    prs: Sequence[Mapping[str, Any]],
) -> tuple[DisambiguationOption, ...]:
    """Mirror ``policy.disambiguate.options_from_prs`` without importing L4."""
    options: list[DisambiguationOption] = []
    for pr in prs:
        number = pr.get("number")
        title = str(pr.get("title") or f"PR {number}")
        key = str(number if number is not None else pr.get("url") or title)
        options.append(
            DisambiguationOption(
                key=key,
                label=f"#{number} {title}" if number is not None else title,
                payload=dict(pr),
            )
        )
    return tuple(options[:_MAX_DISAMBIG_OPTIONS])


def _disambiguation_result(
    *,
    question: str,
    options: Sequence[DisambiguationOption],
    verb: str,
    slots: Mapping[str, Any] | None = None,
    rung: int = 4,
) -> Result:
    """Mirror ``policy.disambiguate.disambiguation_result`` without importing L4."""
    trimmed = tuple(options[:_MAX_DISAMBIG_OPTIONS])
    prompt = DisambiguationPrompt(
        id=uuid.uuid4().hex[:16],
        question=question,
        options=trimmed,
        verb=verb,
        slots=dict(slots or {}),
        expires_at=time.time() + _PENDING_TTL_S,
    )
    labels = "; ".join(
        f"{index + 1}. {opt.label}" for index, opt in enumerate(prompt.options)
    )
    return Result(
        status=Status.NEEDS_DISAMBIGUATE,
        summary=question[:80],
        detail=f"{question} {labels}".strip(),
        evidence=tuple(opt.key for opt in prompt.options),
        rung=rung,
        disambiguation=prompt,
    )


def materialize_forge_argv(
    verb_name: str,
    slots: Mapping[str, Any],
    *,
    context: Context | None = None,
) -> tuple[str, ...] | None:
    """Dry-run / confirm argv shapes for ``forge.*`` verbs."""
    number = _parse_pr_number(slots.get("number"))
    strategy = _normalize_strategy(slots.get("strategy"))
    flag = _STRATEGY_FLAGS[strategy]

    if verb_name == "forge.pr.create":
        return (_GH_BIN, "pr", "create", "--fill")
    if verb_name == "forge.pr.open":
        if number is not None:
            return (_GH_BIN, "pr", "view", str(number), "--web")
        return (_GH_BIN, "pr", "view", "--web")
    if verb_name == "forge.pr.list":
        return (_GH_BIN, "pr", "list")
    if verb_name == "forge.pr.checkout":
        n = str(number) if number is not None else str(slots.get("number") or "<number>")
        return (_GH_BIN, "pr", "checkout", n)
    if verb_name == "forge.pr.merge":
        n = str(number) if number is not None else str(slots.get("number") or "<number>")
        return (_GH_BIN, "pr", "merge", n, flag)
    if verb_name == "forge.auth.login":
        return (_GH_BIN, "auth", "login")
    _ = context
    return None


def build_forge_verbs(
    *,
    run_fn: RunFn | None = None,
    which: WhichFn | None = None,
    packs: PackRegistry | None = None,
    disambiguate: DisambiguateFn | None = None,
) -> tuple[Verb, ...]:
    """Build ``forge.*`` verbs with injectable argv runner / binary seams."""
    runner = run_fn or _default_run
    which_fn: WhichFn = which or shutil.which

    def _run(
        argv: tuple[str, ...],
        *,
        root: Path | None = None,
        timeout: float = 30.0,
    ) -> Any:
        return runner(ArgvCommand(argv=argv, cwd=root, timeout=timeout))

    def _ensure_gh() -> Result | None:
        if packs is not None:
            try:
                packs.require_binary(_GH_BIN)
            except PackPrerequisiteError as exc:
                reason = missing_binary_reason(exc.binary)
                return Result(
                    status=Status.UNSUPPORTED,
                    summary=reason,
                    detail=reason,
                    evidence=(_GH_BIN,),
                    rung=4,
                )
            return None
        if which_fn(_GH_BIN) is None:
            reason = missing_binary_reason(_GH_BIN)
            return Result(
                status=Status.UNSUPPORTED,
                summary=reason,
                detail=reason,
                evidence=(_GH_BIN,),
                rung=4,
            )
        return None

    def _ensure_auth(root: Path | None) -> Result | None:
        completed = _run((_GH_BIN, "auth", "status"), root=root, timeout=15.0)
        if _ok(completed):
            return None
        return Result(
            status=Status.UNSUPPORTED,
            summary=_AUTH_DEGRADED,
            detail=(
                f"{_AUTH_DEGRADED} — say 'login to gh' "
                f"(forge.auth.login). {(_stderr(completed) or _stdout(completed)).strip()[:200]}"
            ).strip(),
            evidence=(_GH_BIN, "auth", "status"),
            rung=4,
        )

    def _preflight(
        intent: Intent,
        context: Context,
        *,
        need_auth: bool = True,
    ) -> tuple[Path | None, Result | None]:
        missing = _ensure_gh()
        if missing is not None:
            return None, missing
        root = _repo_root(context, intent.slots)
        if need_auth:
            auth = _ensure_auth(root)
            if auth is not None:
                return root, auth
        return root, None

    def _list_prs(root: Path | None) -> list[dict[str, Any]]:
        completed = _run(
            (
                _GH_BIN,
                "pr",
                "list",
                "--json",
                "number,title,url,headRefName,baseRefName",
                "--limit",
                "20",
            ),
            root=root,
            timeout=30.0,
        )
        if not _ok(completed):
            return []
        text = _stdout(completed).strip()
        if not text:
            return []
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return []
        if not isinstance(data, list):
            return []
        out: list[dict[str, Any]] = []
        for item in data:
            if isinstance(item, dict):
                out.append(dict(item))
        return out

    def _repo_name(root: Path | None, slots: Mapping[str, Any]) -> str:
        if slots.get("repo"):
            return str(slots["repo"])
        completed = _run(
            (_GH_BIN, "repo", "view", "--json", "nameWithOwner"),
            root=root,
            timeout=20.0,
        )
        if _ok(completed):
            try:
                payload = json.loads(_stdout(completed) or "{}")
            except json.JSONDecodeError:
                payload = {}
            name = payload.get("nameWithOwner") if isinstance(payload, dict) else None
            if name:
                return str(name)
        if root is not None:
            return root.name
        return "<repo>"

    def _default_base(root: Path | None) -> str:
        completed = _run(
            (_GH_BIN, "repo", "view", "--json", "defaultBranchRef"),
            root=root,
            timeout=20.0,
        )
        if _ok(completed):
            try:
                payload = json.loads(_stdout(completed) or "{}")
            except json.JSONDecodeError:
                payload = {}
            ref = payload.get("defaultBranchRef") if isinstance(payload, dict) else None
            if isinstance(ref, dict) and ref.get("name"):
                return str(ref["name"])
        return "main"

    def _head_branch(context: Context, root: Path | None) -> str:
        if context.repo is not None and context.repo.branch:
            return context.repo.branch
        completed = _run(
            ("git", "rev-parse", "--abbrev-ref", "HEAD"),
            root=root,
            timeout=10.0,
        )
        if _ok(completed):
            text = _stdout(completed).strip()
            if text:
                return text
        return "HEAD"

    def _commit_subject(root: Path | None) -> str:
        completed = _run(
            ("git", "log", "-1", "--format=%s"),
            root=root,
            timeout=10.0,
        )
        if _ok(completed):
            text = _stdout(completed).strip()
            if text:
                return text
        return "Pull request"

    def _pr_view(root: Path | None, number: int) -> dict[str, Any] | None:
        completed = _run(
            (
                _GH_BIN,
                "pr",
                "view",
                str(number),
                "--json",
                "number,title,url,headRefName,baseRefName",
            ),
            root=root,
            timeout=20.0,
        )
        if not _ok(completed):
            return None
        try:
            payload = json.loads(_stdout(completed) or "{}")
        except json.JSONDecodeError:
            return None
        return dict(payload) if isinstance(payload, dict) else None

    def _resolve_pr(
        intent: Intent,
        context: Context,
        root: Path | None,
        *,
        verb: str,
    ) -> dict[str, Any] | Result:
        number = _parse_pr_number(intent.slots.get("number"))
        if number is not None:
            viewed = _pr_view(root, number)
            if viewed is not None:
                return viewed
            return {
                "number": number,
                "title": str(intent.slots.get("title") or f"PR {number}"),
            }

        prs = _list_prs(root)
        if not prs:
            return Result(
                status=Status.REFUSED,
                summary="No open pull requests",
                detail="gh pr list returned no open PRs",
                evidence=(_GH_BIN, "pr", "list"),
                rung=4,
            )
        if len(prs) == 1:
            return prs[0]

        options = _options_from_prs(prs)
        keys = tuple(opt.key for opt in options)
        if disambiguate is not None:
            chosen = disambiguate(keys, "Which pull request?")
            if chosen is not None:
                for pr in prs:
                    if str(pr.get("number")) == chosen:
                        return pr
        return _disambiguation_result(
            question="Which pull request?",
            options=options,
            verb=verb,
            slots=dict(intent.slots),
            rung=4,
        )

    def handle_pr_create(intent: Intent, context: Context) -> Result:
        root, err = _preflight(intent, context)
        if err is not None:
            return err
        materialized = (_GH_BIN, "pr", "create", "--fill")
        if "dry_run" in intent.modifiers:
            return Result(
                status=Status.DRY_RUN,
                summary=_display(materialized)[:80],
                detail=_display(materialized),
                evidence=materialized,
                rung=4,
            )

        title = str(intent.slots.get("title") or _commit_subject(root))
        base = str(intent.slots.get("base") or _default_base(root))
        head = str(intent.slots.get("head") or _head_branch(context, root))
        repo = _repo_name(root, intent.slots)
        detail = f"repo={repo} title={title!r} base={base} head={head}"

        if not _confirmed(intent):
            return Result(
                status=Status.NEEDS_CONFIRM,
                summary=f"Create PR: {title}"[:80],
                detail=detail,
                evidence=materialized,
                rung=4,
                pending=_pending(
                    verb="forge.pr.create",
                    slots={
                        "title": title,
                        "base": base,
                        "head": head,
                        "repo": repo,
                        "repo_root": os.fspath(root) if root else None,
                    },
                    materialized=materialized,
                    risk=RiskClass.R2,
                ),
            )

        completed = _run(materialized, root=root, timeout=60.0)
        if not _ok(completed):
            err_text = (_stderr(completed) or _stdout(completed) or "gh failed").strip()
            return Result(
                status=Status.FAILED,
                summary="Could not create pull request",
                detail=err_text[:400],
                evidence=materialized,
                rung=4,
            )
        return Result(
            status=Status.OK,
            summary=f"Created PR: {title}"[:80],
            detail=(_stdout(completed) or detail).strip()[:400],
            evidence=materialized,
            rung=4,
        )

    def handle_pr_open(intent: Intent, context: Context) -> Result:
        root, err = _preflight(intent, context)
        if err is not None:
            return err

        number = _parse_pr_number(intent.slots.get("number"))
        if number is None:
            resolved = _resolve_pr(
                intent, context, root, verb="forge.pr.open"
            )
            if isinstance(resolved, Result):
                if "dry_run" in intent.modifiers and resolved.status is Status.NEEDS_DISAMBIGUATE:
                    return Result(
                        status=Status.DRY_RUN,
                        summary="gh pr view --web",
                        detail="multiple open PRs",
                        evidence=(_GH_BIN, "pr", "view", "--web"),
                        rung=4,
                    )
                return resolved
            number = _parse_pr_number(resolved.get("number"))
            if number is None:
                return Result(
                    status=Status.FAILED,
                    summary="No PR number",
                    detail="Could not resolve a pull request number",
                    rung=4,
                )

        materialized = (_GH_BIN, "pr", "view", str(number), "--web")
        if "dry_run" in intent.modifiers:
            return Result(
                status=Status.DRY_RUN,
                summary=_display(materialized)[:80],
                detail=_display(materialized),
                evidence=materialized,
                rung=4,
            )
        completed = _run(materialized, root=root, timeout=30.0)
        if not _ok(completed):
            return Result(
                status=Status.FAILED,
                summary=f"Could not open PR #{number}"[:80],
                detail=(_stderr(completed) or _stdout(completed)).strip()[:400],
                evidence=materialized,
                rung=4,
            )
        return Result(
            status=Status.OK,
            summary=f"Opened PR #{number}"[:80],
            detail=_display(materialized),
            evidence=materialized,
            rung=4,
        )

    def handle_pr_list(intent: Intent, context: Context) -> Result:
        root, err = _preflight(intent, context)
        if err is not None:
            return err
        materialized = (_GH_BIN, "pr", "list")
        if "dry_run" in intent.modifiers:
            return Result(
                status=Status.DRY_RUN,
                summary=_display(materialized)[:80],
                detail=_display(materialized),
                evidence=materialized,
                rung=4,
            )
        completed = _run(materialized, root=root, timeout=30.0)
        text = (_stdout(completed) or _stderr(completed)).strip()
        if not _ok(completed):
            return Result(
                status=Status.FAILED,
                summary="gh pr list failed",
                detail=text[:400],
                evidence=materialized,
                rung=4,
            )
        lines = [ln for ln in text.splitlines() if ln.strip()]
        summary = f"{len(lines)} open PR(s)" if lines else "No open PRs"
        return Result(
            status=Status.OK,
            summary=summary[:80],
            detail=text or "(none)",
            evidence=materialized,
            rung=4,
        )

    def handle_pr_checkout(intent: Intent, context: Context) -> Result:
        root, err = _preflight(intent, context)
        if err is not None:
            return err
        number = _parse_pr_number(intent.slots.get("number"))
        if number is None:
            return Result(
                status=Status.FAILED,
                summary="No PR number",
                detail="forge.pr.checkout requires number",
                rung=4,
            )
        materialized = (_GH_BIN, "pr", "checkout", str(number))
        if "dry_run" in intent.modifiers:
            return Result(
                status=Status.DRY_RUN,
                summary=_display(materialized)[:80],
                detail=_display(materialized),
                evidence=materialized,
                rung=4,
            )
        completed = _run(materialized, root=root, timeout=60.0)
        if not _ok(completed):
            return Result(
                status=Status.FAILED,
                summary=f"Could not check out PR #{number}"[:80],
                detail=(_stderr(completed) or _stdout(completed)).strip()[:400],
                evidence=materialized,
                rung=4,
            )
        return Result(
            status=Status.OK,
            summary=f"Checked out PR #{number}"[:80],
            detail=(_stdout(completed) or _display(materialized)).strip()[:400],
            evidence=materialized,
            rung=4,
        )

    def handle_pr_merge(intent: Intent, context: Context) -> Result:
        root, err = _preflight(intent, context)
        if err is not None:
            return err

        strategy = _normalize_strategy(intent.slots.get("strategy"))
        flag = _STRATEGY_FLAGS[strategy]
        resolved = _resolve_pr(intent, context, root, verb="forge.pr.merge")
        if isinstance(resolved, Result):
            return resolved
        number = _parse_pr_number(resolved.get("number"))
        if number is None:
            return Result(
                status=Status.FAILED,
                summary="No PR number",
                detail="forge.pr.merge could not resolve a PR number",
                rung=4,
            )
        title = str(resolved.get("title") or intent.slots.get("title") or f"PR {number}")
        repo = _repo_name(root, {**intent.slots, **resolved})
        materialized = (_GH_BIN, "pr", "merge", str(number), flag)
        detail = (
            f"repo={repo} number={number} title={title!r} strategy={strategy}\n"
            f"{_display(materialized)}"
        )

        if "dry_run" in intent.modifiers:
            return Result(
                status=Status.DRY_RUN,
                summary=_display(materialized)[:80],
                detail=detail,
                evidence=materialized,
                rung=4,
            )
        if not _confirmed(intent):
            return Result(
                status=Status.NEEDS_CONFIRM,
                summary=f"Merge PR #{number} ({strategy})?"[:80],
                detail=detail,
                evidence=materialized,
                rung=4,
                pending=_pending(
                    verb="forge.pr.merge",
                    slots={
                        "number": number,
                        "title": title,
                        "repo": repo,
                        "strategy": strategy,
                        "repo_root": os.fspath(root) if root else None,
                    },
                    materialized=materialized,
                    risk=RiskClass.R3,
                ),
            )

        completed = _run(materialized, root=root, timeout=60.0)
        if not _ok(completed):
            return Result(
                status=Status.FAILED,
                summary=f"Could not merge PR #{number}"[:80],
                detail=(_stderr(completed) or _stdout(completed)).strip()[:400],
                evidence=materialized,
                rung=4,
            )
        return Result(
            status=Status.OK,
            summary=f"Merged PR #{number}"[:80],
            detail=detail,
            evidence=materialized,
            rung=4,
        )

    def handle_auth_login(intent: Intent, context: Context) -> Result:
        # Auth login must work when not logged in; only require the binary.
        root, err = _preflight(intent, context, need_auth=False)
        if err is not None:
            return err
        materialized = (_GH_BIN, "auth", "login")
        if "dry_run" in intent.modifiers:
            return Result(
                status=Status.DRY_RUN,
                summary=_display(materialized)[:80],
                detail="Hand off to interactive gh auth login",
                evidence=materialized,
                rung=4,
            )
        # Interactive handoff: run gh and get out of the way.
        completed = _run(materialized, root=root, timeout=600.0)
        if not _ok(completed):
            return Result(
                status=Status.FAILED,
                summary="gh auth login failed",
                detail=(_stderr(completed) or _stdout(completed) or "auth login failed").strip()[
                    :400
                ],
                evidence=materialized,
                rung=4,
            )
        return Result(
            status=Status.OK,
            summary="gh auth login complete",
            detail="Handed off to interactive gh auth login",
            evidence=materialized,
            rung=4,
        )

    return (
        Verb(
            name="forge.pr.create",
            title="Create a pull request",
            slots={
                "title": SlotSpec(type="str", required=False),
                "base": SlotSpec(type="str", required=False),
            },
            rung=4,
            risk=RiskClass.R2,
            requires=frozenset({"repo"}),
            support=_ALL_SUPPORT,
            undo=None,
            pack=PACK_NAME,
            handler=handle_pr_create,
        ),
        Verb(
            name="forge.pr.open",
            title="Open a pull request in the browser",
            slots={"number": SlotSpec(type="int", required=False)},
            rung=4,
            risk=RiskClass.R0,
            requires=frozenset({"repo"}),
            support=_ALL_SUPPORT,
            undo=None,
            pack=PACK_NAME,
            handler=handle_pr_open,
        ),
        Verb(
            name="forge.pr.list",
            title="List open pull requests",
            slots={},
            rung=4,
            risk=RiskClass.R0,
            requires=frozenset({"repo"}),
            support=_ALL_SUPPORT,
            undo=None,
            pack=PACK_NAME,
            handler=handle_pr_list,
        ),
        Verb(
            name="forge.pr.checkout",
            title="Check out a pull request branch",
            slots={"number": SlotSpec(type="int", required=True)},
            rung=4,
            risk=RiskClass.R1,
            requires=frozenset({"repo"}),
            support=_ALL_SUPPORT,
            undo=None,
            pack=PACK_NAME,
            handler=handle_pr_checkout,
        ),
        Verb(
            name="forge.pr.merge",
            title="Merge a pull request",
            slots={
                "number": SlotSpec(type="int", required=False),
                "strategy": SlotSpec(
                    type="str", required=False, default=_DEFAULT_STRATEGY
                ),
            },
            rung=4,
            risk=RiskClass.R3,
            requires=frozenset({"repo"}),
            support=_ALL_SUPPORT,
            undo=None,
            pack=PACK_NAME,
            handler=handle_pr_merge,
        ),
        Verb(
            name="forge.auth.login",
            title="Log in to GitHub CLI",
            slots={},
            rung=4,
            risk=RiskClass.R1,
            requires=frozenset(),
            support=_ALL_SUPPORT,
            undo=None,
            pack=PACK_NAME,
            handler=handle_auth_login,
        ),
    )


def register_forge_pack(
    registry: Registry,
    *,
    run_fn: RunFn | None = None,
    which: WhichFn | None = None,
    packs: PackRegistry | None = None,
    disambiguate: DisambiguateFn | None = None,
) -> tuple[Pattern, ...]:
    """Register ``forge.*`` verbs; return grammar patterns."""
    for verb in build_forge_verbs(
        run_fn=run_fn,
        which=which,
        packs=packs,
        disambiguate=disambiguate,
    ):
        registry.register(verb)
    return forge_patterns()
