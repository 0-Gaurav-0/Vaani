"""Container / docker pack stub (T4.4). Descriptors live in ``packs.registry``."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vaani.verbs.registry import Registry

PACK_NAME = "docker"


def register_docker_pack(registry: Registry) -> None:
    """No verbs yet — T4.4 registers ``container.*`` handlers here."""
    _ = registry
