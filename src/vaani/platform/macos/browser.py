"""Open URLs with Brave, Chrome, or the macOS default handler."""
from __future__ import annotations

import subprocess
from typing import Any, Callable


_BROWSER_APPS = {
    "brave": ("Brave Browser",),
    "chrome": ("Google Chrome", "Chrome"),
}


class MacBrowserLauncher:
    def __init__(self, *, runner: Callable[..., Any] | None = None):
        self._runner = runner or subprocess.run

    def open(self, url: str, *, prefer: str | None = None) -> str:
        prefer_key = (prefer or "brave").casefold()
        if prefer_key not in _BROWSER_APPS:
            prefer_key = "brave"
        order = [prefer_key] + [key for key in _BROWSER_APPS if key != prefer_key]
        for key in order:
            for app_name in _BROWSER_APPS[key]:
                if self._try_open_app(app_name, url):
                    return "Opened browser."
        if self._try_open_default(url):
            return "Opened browser."
        return "Unable to open browser."

    def _try_open_app(self, app_name: str, url: str) -> bool:
        try:
            result = self._runner(
                ["open", "-a", app_name, url],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            return False
        return getattr(result, "returncode", 1) == 0

    def _try_open_default(self, url: str) -> bool:
        try:
            result = self._runner(
                ["open", url],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            return False
        return getattr(result, "returncode", 1) == 0


def open_browser(*, prefer_brave: bool = True, url: str = "about:blank") -> str:
    prefer = "brave" if prefer_brave else "chrome"
    return MacBrowserLauncher().open(url, prefer=prefer)
