"""Linux browser launch helpers for assistant site intents."""
from __future__ import annotations

import logging
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger("vaani.browser")


class LinuxBrowserLauncher:
    def open(self, url: str, *, prefer: str | None = None) -> str:
        prefer_brave = (prefer or "brave").casefold() != "chrome"
        return open_browser(prefer_brave=prefer_brave, url=url)


def _is_youtube_url(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").casefold()
    except Exception:
        return False
    return host.endswith("youtube.com") or host.endswith("youtu.be")


def open_browser(*, prefer_brave: bool = True, url: str = "about:blank") -> str:
    # Reuse the existing YouTube tab when possible (avoids dual-audio new tabs).
    if _is_youtube_url(url):
        try:
            from .youtube_tab import navigate_youtube_tab

            if navigate_youtube_tab(url):
                return "Opened browser."
        except Exception as exc:
            logger.info(
                "event=youtube_reuse_skipped detail=%s", type(exc).__name__
            )
        # Still pause current MPRIS audio before opening a fresh tab.
        try:
            from ...mpris import mpris_pause_all

            mpris_pause_all()
        except Exception:
            pass

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
        # No --new-window: reuse the running browser (still may add a tab if
        # YouTube-tab navigate above missed).
        if "brave" in executable:
            args = [executable, url]
        else:
            args = [executable, "--profile-directory=Default", url]
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
