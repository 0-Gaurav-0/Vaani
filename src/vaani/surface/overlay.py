"""Guide overlay surface: file-backed IPC and test doubles."""
from __future__ import annotations

import time
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from vaani.indicator_protocol import (
    clear_overlay,
    overlay_path,
    write_overlay,
)
from vaani.intent.schema import OverlayOp


class OverlaySurface(Protocol):
    def show(self, ops: Sequence[OverlayOp], *, ttl: float = 8.0) -> None: ...

    def clear(self) -> None: ...


class FileOverlay:
    """Write overlay ops for the indicator process to render."""

    def __init__(self, cache_dir: Path | str) -> None:
        self._path = overlay_path(cache_dir)

    def show(self, ops: Sequence[OverlayOp], *, ttl: float = 8.0) -> None:
        write_overlay(self._path, ops, expires_at=time.time() + ttl)

    def clear(self) -> None:
        clear_overlay(self._path)


class FakeOverlay:
    """In-memory overlay for unit tests."""

    def __init__(self) -> None:
        self.shown: list[tuple[tuple[OverlayOp, ...], float]] = []
        self.clear_count = 0

    def show(self, ops: Sequence[OverlayOp], *, ttl: float = 8.0) -> None:
        self.shown.append((tuple(ops), ttl))

    def clear(self) -> None:
        self.clear_count += 1
