"""Windows PlatformBundle assembly — filled by P1-03 implementation."""
from __future__ import annotations

from ...config import Settings
from ..protocol import PlatformBundle, UnsupportedPlatform


def build_windows(settings: Settings | None = None) -> PlatformBundle:
    raise UnsupportedPlatform(
        "Windows adapter modules are incomplete; implement platform/windows/* (P1-03)"
    )
