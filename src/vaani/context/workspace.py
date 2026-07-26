"""Workspace resolution with explicit source reporting (spec §5.2)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

# Spec §5.2 source tags — always echo onto Result.workspace_source (invariant 7).
SOURCE_FOCUSED_EDITOR = "focused-editor"
SOURCE_FOCUSED_TERMINAL = "focused-terminal"
SOURCE_CONFIG = "config"
SOURCE_LAST_USED = "last-used"
SOURCE_HOME = "home"

_ENV_CWD = "VAANI_ASSISTANT_CWD"

# Process-local "last workspace used this boot".
_last_used: Path | None = None


def get_last_used() -> Path | None:
    return _last_used


def set_last_used(path: Path | None) -> None:
    global _last_used
    _last_used = path.resolve() if path is not None else None


def clear_last_used() -> None:
    set_last_used(None)


def _existing_dir(path: Path | None) -> Path | None:
    if path is None:
        return None
    try:
        resolved = path.expanduser().resolve()
    except OSError:
        return None
    if resolved.is_dir():
        return resolved
    return None


def resolve_workspace(
    *,
    editor_project: Path | None = None,
    terminal_cwd: Path | None = None,
    environ: Mapping[str, str] | None = None,
    last_used: Path | None | object = ...,
    home: Path | None = None,
) -> tuple[Path, str]:
    """Return ``(workspace, workspace_source)`` using §5.2 precedence.

    Order: focused-editor → focused-terminal → ``VAANI_ASSISTANT_CWD``
    → last-used this boot → ``$HOME``.
    """
    env = os.environ if environ is None else environ
    if last_used is ...:
        cached = get_last_used()
    else:
        cached = last_used  # type: ignore[assignment]
    home_path = Path.home() if home is None else home

    for candidate, source in (
        (_existing_dir(editor_project), SOURCE_FOCUSED_EDITOR),
        (_existing_dir(terminal_cwd), SOURCE_FOCUSED_TERMINAL),
        (_existing_dir(Path(env[_ENV_CWD]) if env.get(_ENV_CWD) else None), SOURCE_CONFIG),
        (_existing_dir(cached), SOURCE_LAST_USED),
    ):
        if candidate is not None:
            return candidate, source

    home_resolved = _existing_dir(home_path) or home_path.expanduser()
    return home_resolved, SOURCE_HOME
