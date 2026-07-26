"""Request context providers: workspace, repo, focus (spec §5.2 / T3.2)."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from vaani.context.focus import (
    ActiveFocus,
    FakeFocusProbe,
    FocusProbe,
    NullFocusProbe,
    probe_for_platform,
    resolve_focus,
)
from vaani.context.project import (
    CHECKED_FILES,
    ProfileMiss,
    clear_project_cache,
    detect_project,
    require_command,
)
from vaani.context.repo import resolve_repo
from vaani.context.workspace import (
    SOURCE_CONFIG,
    SOURCE_FOCUSED_EDITOR,
    SOURCE_FOCUSED_TERMINAL,
    SOURCE_HOME,
    SOURCE_LAST_USED,
    clear_last_used,
    get_last_used,
    resolve_workspace,
    set_last_used,
)
from vaani.intent.schema import Context, ProjectProfile, RepoInfo
from vaani.platform.protocol import PlatformId

# Runner is injected from L5 by callers — context must not import vaani.exec.
RunFn = Callable[..., Any]


def build_context(
    platform: PlatformId,
    *,
    focus_probe: FocusProbe | None = None,
    runner: RunFn | None = None,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
    last_used: Path | None | object = ...,
    remember: bool = True,
    include_focus: bool = True,
    include_repo: bool = True,
    include_project: bool = True,
) -> Context:
    """Build a Context with best-effort workspace / repo / focus / project.

    Workspace (and ``workspace_source``) are always resolved. Focus is populated
    when available. Repo requires an injected argv ``runner`` (typically
    ``vaani.exec.runner.run``); without one, ``repo`` stays ``None``.
    Project profile is detected when a workspace path is known (spec §12.8);
    a detection miss leaves ``project`` as ``None`` (verbs refuse via
    :func:`detect_project` / :func:`require_command`).
    """
    if focus_probe is not None:
        active = resolve_focus(focus_probe)
    elif include_focus:
        active = resolve_focus(probe_for_platform(platform))
    else:
        active = ActiveFocus()

    editor_project = active.project_root if active.role == "editor" else None
    terminal_cwd = active.cwd if active.role == "terminal" else None

    workspace, source = resolve_workspace(
        editor_project=editor_project,
        terminal_cwd=terminal_cwd,
        environ=environ,
        last_used=last_used,
        home=home,
    )

    # Don't let a pure home fallback clobber a real last-used path.
    if remember and source != SOURCE_HOME:
        set_last_used(workspace)

    repo: RepoInfo | None = None
    if include_repo and runner is not None:
        repo = resolve_repo(workspace, runner=runner)

    project: ProjectProfile | None = None
    if include_project and workspace is not None:
        detected = detect_project(workspace)
        if isinstance(detected, ProjectProfile):
            project = detected

    focus_info = active.info if include_focus else None

    return Context(
        platform=platform,
        workspace=workspace,
        workspace_source=source,
        repo=repo,
        project=project,
        focus=focus_info,
        screen=None,
        session=None,
    )


__all__ = [
    "ActiveFocus",
    "CHECKED_FILES",
    "FakeFocusProbe",
    "FocusProbe",
    "NullFocusProbe",
    "ProfileMiss",
    "SOURCE_CONFIG",
    "SOURCE_FOCUSED_EDITOR",
    "SOURCE_FOCUSED_TERMINAL",
    "SOURCE_HOME",
    "SOURCE_LAST_USED",
    "build_context",
    "clear_last_used",
    "clear_project_cache",
    "detect_project",
    "get_last_used",
    "probe_for_platform",
    "require_command",
    "resolve_focus",
    "resolve_repo",
    "resolve_workspace",
    "set_last_used",
]
