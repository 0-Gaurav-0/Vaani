"""Linux browser launch helpers for assistant site intents."""
from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path


class LinuxBrowserLauncher:
    def open(self, url: str, *, prefer: str | None = None) -> str:
        prefer_brave = (prefer or "brave").casefold() != "chrome"
        return open_browser(prefer_brave=prefer_brave, url=url)


def open_browser(*, prefer_brave: bool = True, url: str = "about:blank") -> str:
    brave_names = ("/opt/brave.com/brave/brave-browser", "brave-browser", "brave")
    other_names = ("google-chrome", "chromium", "chromium-browser")
    names = brave_names + other_names if prefer_brave else other_names + brave_names
    executable = None
    for name in names:
        found = shutil.which(name)
        if found:
            executable = found
            break
        if name.startswith("/") and Path(name).exists():
            executable = name
            break
    if not executable:
        return "Unable to open browser: no supported browser is installed."
    try:
        args = (
            [executable, "--new-window", url]
            if "brave" in executable
            else [executable, "--profile-directory=Default", "--new-window", url]
        )
        proc = subprocess.Popen(
            args,
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(0.4)
        return (
            "Opened browser."
            if proc.poll() is None
            else "Browser launch exited; check whether an existing browser window was reused."
        )
    except OSError:
        return "Unable to open browser."
