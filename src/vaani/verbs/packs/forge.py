"""Forge / ``gh`` pack stub (T4.3). Descriptors live in ``packs.registry``."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vaani.verbs.registry import Registry

PACK_NAME = "forge"


def register_forge_pack(registry: Registry) -> None:
    """No verbs yet — T4.3 registers ``forge.*`` handlers here."""
    _ = registry
