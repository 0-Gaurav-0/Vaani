"""Package-manager pack stub (T4.4). Descriptors live in ``packs.registry``."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vaani.verbs.registry import Registry

PACK_NAME = "pkg"


def register_pkg_pack(registry: Registry) -> None:
    """No verbs yet — T4.4 registers ``pkg.*`` handlers here."""
    _ = registry
