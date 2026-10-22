"""Small helpers shared by the daemon side and the worker (stdlib only)."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


def default_root() -> Path:
    try:
        docs = subprocess.run(["xdg-user-dir", "DOCUMENTS"], capture_output=True, text=True, timeout=3).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        docs = ""
    return Path(docs or Path.home() / "Documents") / "Vaani Meetings"


def gemini_key() -> str | None:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if key:
        return key
    env_file = Path.home() / ".config" / "vaani" / "vaani.env"
    try:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("GEMINI_API_KEY="):
                return line.split("=", 1)[1].strip().strip("\"'") or None
    except OSError:
        pass
    return None


def notify(title: str, body: str, *, sticky: bool = False) -> None:
    argv = ["notify-send", "-a", "Vaani", *(["-u", "critical"] if sticky else []), title, body]
    try:
        subprocess.run(argv, check=False, timeout=5)
    except (OSError, subprocess.SubprocessError):
        pass
