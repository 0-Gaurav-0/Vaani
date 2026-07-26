"""Git / VCS pack stub (T4.2). Descriptors live in ``packs.registry``."""
from __future__ import annotations

from vaani.intent.grammar import Pattern
from vaani.verbs.registry import Registry

PACK_NAME = "git"


def register_git_pack(registry: Registry) -> tuple[Pattern, ...]:
    """No verbs yet — T4.2 registers ``vcs.*`` handlers here."""
    _ = registry
    return ()
