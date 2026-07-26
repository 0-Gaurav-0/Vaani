"""Linux app launcher wrapping the existing catalog."""
from __future__ import annotations

from ... import apps as _apps


class LinuxAppLauncher:
    def resolve(self, command: str):
        return _apps.resolve_app(command)

    def launch(self, target) -> str:
        return _apps.launch_app(target)
