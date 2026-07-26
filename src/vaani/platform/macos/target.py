"""Frontmost-application focus probe for macOS paste safety."""
from __future__ import annotations

import subprocess
from typing import Callable

from ..protocol import FocusSnapshot

_FRONTMOST_SCRIPT = (
    'tell application "System Events" to '
    "get {name, unix id} of first application process whose frontmost is true"
)


def _default_runner(args: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, **kwargs)


class MacTargetProbe:
    """Snapshot the frontmost app name + pid as an opaque focus token."""

    def __init__(
        self,
        *,
        runner: Callable[..., subprocess.CompletedProcess[str]] = _default_runner,
    ):
        self._runner = runner

    def snapshot(self) -> FocusSnapshot | None:
        try:
            result = self._runner(
                ["osascript", "-e", _FRONTMOST_SCRIPT],
                check=False,
                capture_output=True,
                text=True,
                timeout=2.0,
            )
        except Exception:
            return None
        if getattr(result, "returncode", 1) != 0:
            return None
        stdout = (getattr(result, "stdout", None) or "").strip()
        if not stdout:
            return None
        # osascript prints "Name, 12345"
        parts = [part.strip() for part in stdout.split(",", 1)]
        if len(parts) != 2 or not parts[0] or not parts[1]:
            return None
        return FocusSnapshot(token=f"{parts[0]}|{parts[1]}")

    def unchanged(self, before: FocusSnapshot) -> bool:
        if before is None:
            return False
        current = self.snapshot()
        return current is not None and current == before

    capture = snapshot
    probe = snapshot


TargetProbe = MacTargetProbe
