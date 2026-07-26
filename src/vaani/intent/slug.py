"""Branch / path slugification for spoken names (spec §9.1 / A-03).

Operates on already-normalized text when possible. Separator words
(``slash`` / ``dash`` / ``underscore``) are handled by
:func:`vaani.intent.normalize.normalize` before slugification.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from enum import Enum

_SPACE_RE = re.compile(r"[\s]+")
_MULTI_SEP_RE = re.compile(r"[-_]{2,}")
_INVALID_REF_CHARS = re.compile(r"[\x00-\x1f\x7f ~^:?*\[\\]")


class BranchConvention(str, Enum):
    KEBAB = "kebab"  # lower-kebab-case
    SNAKE = "snake"  # lower_snake_case


def infer_branch_convention(branches: Sequence[str]) -> BranchConvention:
    """Infer kebab vs snake from existing local branch names.

    Ignores ``main`` / ``master`` / ``HEAD`` and names without separators.
    Defaults to kebab when the signal is mixed or empty.
    """
    kebab = 0
    snake = 0
    for raw in branches:
        name = raw.strip().lstrip("*").strip()
        if not name or name in {"main", "master", "HEAD", "develop", "trunk"}:
            continue
        # Only the final path segment carries the convention signal.
        segment = name.rsplit("/", 1)[-1]
        has_dash = "-" in segment
        has_under = "_" in segment
        if has_dash and not has_under:
            kebab += 1
        elif has_under and not has_dash:
            snake += 1
    if snake > kebab:
        return BranchConvention.SNAKE
    return BranchConvention.KEBAB


def slugify_branch(
    text: str,
    *,
    convention: BranchConvention | None = None,
    branches: Sequence[str] | None = None,
) -> str:
    """Turn spoken / normalized text into a git branch name.

    Examples:
    - ``"assistant use cases"`` → ``assistant-use-cases``
    - ``"feature/port killer"`` (after normalize of "feature slash …") →
      ``feature/port-killer``
    """
    conv = convention
    if conv is None:
        conv = (
            infer_branch_convention(branches)
            if branches is not None
            else BranchConvention.KEBAB
        )

    raw = " ".join(str(text or "").strip().split())
    if not raw:
        return ""

    # Preserve explicit path segments (slash already materialised by normalize).
    parts = [p for p in raw.split("/") if p != ""]
    sep = "-" if conv is BranchConvention.KEBAB else "_"
    other = "_" if sep == "-" else "-"
    slugged: list[str] = []
    for part in parts:
        piece = part.casefold()
        piece = piece.replace(other, sep)
        piece = _SPACE_RE.sub(sep, piece)
        piece = _MULTI_SEP_RE.sub(sep, piece)
        piece = piece.strip(sep)
        # Drop characters git-check-ref-format rejects in a branch name.
        piece = _INVALID_REF_CHARS.sub("", piece)
        piece = piece.replace("..", sep)
        piece = piece.rstrip(".")
        piece = _MULTI_SEP_RE.sub(sep, piece).strip(sep)
        if piece:
            slugged.append(piece)
    return "/".join(slugged)


def is_plausible_ref(name: str) -> bool:
    """Cheap local rejection before shelling out to ``git check-ref-format``."""
    if not name or name in {".", ".."}:
        return False
    if name.startswith("/") or name.endswith("/") or "//" in name:
        return False
    if name.endswith(".lock"):
        return False
    if "@{" in name or "\\" in name:
        return False
    if _INVALID_REF_CHARS.search(name):
        return False
    if ".." in name or name.endswith("."):
        return False
    return True


def branch_names_from_list(output: str) -> tuple[str, ...]:
    """Parse ``git branch`` / ``git branch --list`` stdout into bare names."""
    names: list[str] = []
    for line in (output or "").splitlines():
        name = line.strip().lstrip("*").strip()
        if name and name != "HEAD":
            names.append(name)
    return tuple(names)


def majority_convention(samples: Iterable[str]) -> BranchConvention:
    """Alias for :func:`infer_branch_convention` (clarity at call sites)."""
    return infer_branch_convention(tuple(samples))
