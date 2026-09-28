"""Linux/X11 platform adapters (current production backend)."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..protocol import PlatformBundle
    from ...config import Settings

__all__ = ["build_linux"]


def build_linux(settings: "Settings | None" = None) -> "PlatformBundle":
    from .runtime import build_linux as _build_linux

    return _build_linux(settings)
