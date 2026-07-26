"""macOS PlatformBundle assembly — filled by P1-02 implementation."""
from __future__ import annotations

from ...config import Settings
from ..protocol import PlatformBundle, UnsupportedPlatform


def build_macos(settings: Settings | None = None) -> PlatformBundle:
    raise UnsupportedPlatform(
        "macOS adapter modules are incomplete; implement platform/macos/* (P1-02)"
    )
