"""OS detection and PlatformBundle factory."""
from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from .protocol import PlatformId, PlatformBundle, UnsupportedPlatform

if TYPE_CHECKING:
    from ..config import Settings

__all__ = [
    "PlatformId",
    "PlatformBundle",
    "UnsupportedPlatform",
    "detect_os",
    "build_platform",
]


def detect_os(platform: str | None = None) -> PlatformId:
    value = (platform if platform is not None else sys.platform).casefold()
    if value.startswith("linux"):
        return PlatformId.LINUX
    if value == "darwin":
        return PlatformId.MACOS
    if value in {"win32", "cygwin", "msys"}:
        return PlatformId.WINDOWS
    raise UnsupportedPlatform(f"unsupported platform: {value}")


def build_platform(settings: Settings | None = None) -> PlatformBundle:
    """Construct the desktop I/O bundle for the current operating system."""
    os_id = detect_os()
    if os_id is PlatformId.LINUX:
        from .linux.runtime import build_linux

        return build_linux(settings)
    if os_id is PlatformId.MACOS:
        raise UnsupportedPlatform(
            "macOS adapter is not implemented yet; see docs/install once P1-02 lands"
        )
    if os_id is PlatformId.WINDOWS:
        raise UnsupportedPlatform(
            "Windows adapter is not implemented yet; see docs/install once P1-03 lands"
        )
    raise UnsupportedPlatform(f"unsupported platform: {os_id}")
