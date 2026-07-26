"""Windows browser launch helpers for assistant site intents."""
from __future__ import annotations

import os
import shutil
import subprocess
import time
import webbrowser
from pathlib import Path
from typing import Any, Callable


def _existing(paths: list[Path | str]) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for raw in paths:
        path = Path(raw)
        which = shutil.which(str(raw))
        candidate = which or (str(path) if path.is_file() else None)
        if not candidate:
            continue
        key = candidate.casefold()
        if key in seen:
            continue
        seen.add(key)
        found.append(candidate)
    return found


def _brave_candidates() -> list[str]:
    local = os.environ.get("LOCALAPPDATA", "")
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    return _existing(
        [
            "brave.exe",
            "brave",
            Path(local) / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe",
            Path(program_files) / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe",
            Path(program_files_x86) / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe",
        ]
    )


def _chrome_candidates() -> list[str]:
    local = os.environ.get("LOCALAPPDATA", "")
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    return _existing(
        [
            "chrome.exe",
            "chrome",
            Path(local) / "Google" / "Chrome" / "Application" / "chrome.exe",
            Path(program_files) / "Google" / "Chrome" / "Application" / "chrome.exe",
            Path(program_files_x86) / "Google" / "Chrome" / "Application" / "chrome.exe",
        ]
    )


class WindowsBrowserLauncher:
    def __init__(
        self,
        *,
        popen: Callable[..., Any] = subprocess.Popen,
        open_url: Callable[[str], bool] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        brave_finder: Callable[[], list[str]] | None = None,
        chrome_finder: Callable[[], list[str]] | None = None,
    ):
        self._popen = popen
        self._open_url = open_url or webbrowser.open
        self._sleep = sleeper
        self._brave_finder = brave_finder or _brave_candidates
        self._chrome_finder = chrome_finder or _chrome_candidates

    def open(self, url: str, *, prefer: str | None = None) -> str:
        prefer_brave = (prefer or "brave").casefold() != "chrome"
        return open_browser(
            prefer_brave=prefer_brave,
            url=url,
            popen=self._popen,
            open_url=self._open_url,
            sleeper=self._sleep,
            brave_finder=self._brave_finder,
            chrome_finder=self._chrome_finder,
        )


def open_browser(
    *,
    prefer_brave: bool = True,
    url: str = "about:blank",
    popen: Callable[..., Any] = subprocess.Popen,
    open_url: Callable[[str], bool] | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    brave_finder: Callable[[], list[str]] | None = None,
    chrome_finder: Callable[[], list[str]] | None = None,
) -> str:
    brave = (brave_finder or _brave_candidates)()
    chrome = (chrome_finder or _chrome_candidates)()
    ordered = brave + chrome if prefer_brave else chrome + brave
    for executable in ordered:
        try:
            proc = popen(
                [executable, "--new-window", url],
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            sleeper(0.4)
            return (
                "Opened browser."
                if getattr(proc, "poll", lambda: None)() is None
                else "Browser launch exited; check whether an existing browser window was reused."
            )
        except OSError:
            continue
    opener = open_url or webbrowser.open
    try:
        if opener(url):
            return "Opened browser."
    except Exception:
        pass
    return "Unable to open browser."
