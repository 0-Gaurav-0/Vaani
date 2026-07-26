"""Git / VCS pack stub (T4.2). Descriptors live in ``packs.registry``."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vaani.verbs.registry import Registry

PACK_NAME = "git"


def register_git_pack(registry: Registry) -> None:
    """No verbs yet — T4.2 registers ``vcs.*`` handlers here."""
    _ = registry
