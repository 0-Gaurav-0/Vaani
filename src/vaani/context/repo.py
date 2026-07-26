"""Git repo root discovery via an injected argv runner (no shell)."""
from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from vaani.intent.schema import RepoInfo


@dataclass(frozen=True)
class _Cmd:
    """Minimal command shape accepted by ``resolve_repo`` runners.

    Duck-types ``vaani.exec.runner.Command`` so callers can pass ``run`` directly.
    """

    argv: tuple[str, ...]
    cwd: Path | None = None
    timeout: float = 5.0
    env_extra: dict[str, str] | None = None


class _CompletedLike(Protocol):
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    cancelled: bool


RunFn = Callable[..., _CompletedLike]


def resolve_repo(
    start: Path | None,
    *,
    runner: RunFn,
) -> RepoInfo | None:
    """Resolve ``RepoInfo`` from ``start`` (or cwd) via ``git rev-parse``.

    Uses argv-only ``git -C <dir> rev-parse --show-toplevel`` through the
    injected runner — never ``shell=True``. Callers pass ``vaani.exec.runner.run``
    (L5); this L2 module does not import exec (layering).
    """
    base = start if start is not None else Path.cwd()
    try:
        cwd = base.expanduser().resolve()
    except OSError:
        return None
    if not cwd.is_dir():
        return None

    root_s = os.fspath(cwd)
    completed = runner(_Cmd(argv=("git", "-C", root_s, "rev-parse", "--show-toplevel")))
    if completed.returncode != 0 or completed.timed_out or completed.cancelled:
        return None
    top = (completed.stdout or "").strip()
    if not top:
        return None
    root = Path(top)
    if not root.is_dir():
        return None

    root_arg = os.fspath(root)
    branch = _git_text(
        runner, ("git", "-C", root_arg, "rev-parse", "--abbrev-ref", "HEAD")
    )
    dirty = _git_dirty(runner, root_arg)
    upstream = _git_text(
        runner,
        ("git", "-C", root_arg, "rev-parse", "--abbrev-ref", "@{upstream}"),
    )
    remote_host = _remote_host(runner, root_arg)
    return RepoInfo(
        root=root,
        branch=branch,
        dirty=dirty,
        upstream=upstream,
        remote_host=remote_host,
    )


def _git_text(runner: RunFn, argv: tuple[str, ...]) -> str | None:
    completed = runner(_Cmd(argv=argv))
    if completed.returncode != 0:
        return None
    text = (completed.stdout or "").strip()
    return text or None


def _git_dirty(runner: RunFn, root_arg: str) -> bool:
    completed = runner(_Cmd(argv=("git", "-C", root_arg, "status", "--porcelain")))
    if completed.returncode != 0:
        return False
    return bool((completed.stdout or "").strip())


def _remote_host(runner: RunFn, root_arg: str) -> str | None:
    url = _git_text(
        runner,
        ("git", "-C", root_arg, "config", "--get", "remote.origin.url"),
    )
    if not url:
        return None
    if url.startswith("git@"):
        host = url.split(":", 1)[0].removeprefix("git@")
        return host or None
    if "://" in url:
        rest = url.split("://", 1)[1]
        host = rest.split("/", 1)[0]
        if "@" in host:
            host = host.rsplit("@", 1)[-1]
        return host or None
    return None
