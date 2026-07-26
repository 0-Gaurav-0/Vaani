"""Forge / ``gh`` pack stub (T4.3). Descriptors live in ``packs.registry``."""
from __future__ import annotations

from vaani.intent.grammar import Pattern
from vaani.verbs.registry import Registry

PACK_NAME = "forge"


def register_forge_pack(registry: Registry) -> tuple[Pattern, ...]:
    """No verbs yet — T4.3 registers ``forge.*`` handlers here."""
    _ = registry
    return ()
