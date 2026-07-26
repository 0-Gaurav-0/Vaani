"""Windows setup / permission guidance for mic, hotkeys, and paste."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def python_paths() -> list[str]:
    """Binaries Windows may attribute the hotkey listener to."""
    exe = Path(sys.executable).resolve()
    paths = [str(exe)]
    try:
        for name in ("python.exe", "python3.exe"):
            candidate = Path(sys.prefix) / "Scripts" / name
            if not candidate.exists():
                candidate = Path(sys.prefix) / name
            if candidate.exists():
                paths.append(str(candidate.resolve()))
    except OSError:
        pass
    seen: set[str] = set()
    out: list[str] = []
    for path in paths:
        key = path.lower()
        if key not in seen:
            seen.add(key)
            out.append(path)
    return out


def open_privacy_settings() -> None:
    """Best-effort open the Microphone privacy settings pane."""
    try:
        subprocess.run(
            ["cmd", "/c", "start", "", "ms-settings:privacy-microphone"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass


def setup_help_text(*, brief: bool = False) -> str:
    """User-facing guidance printed at startup or on demand."""
    if brief:
        return (
            "[vaani] Hold Ctrl+Space to dictate (release to stop). "
            "Mic privacy must allow desktop apps; see docs/install/windows.md"
        )
    paths = "\n".join(f"  - {p}" for p in python_paths())
    parent = os.environ.get("TERM_PROGRAM") or os.environ.get("WT_SESSION") or "Terminal"
    return f"""Windows setup checklist for Vaani global hotkeys + mic:

1) Settings → Privacy & security → Microphone
   - Allow microphone access
   - Allow desktop apps to access your microphone

2) Run Vaani from a normal (non-elevated) user session.
   Elevated / UIPI-protected windows may ignore synthetic Ctrl+V
   (text stays on the clipboard as CLIPBOARD_ONLY).

3) If hotkeys never fire, antivirus or accessibility tooling may be
   blocking pynput. Allow the Python binary that runs Vaani:
{paths}

4) Hold-to-talk chords (release stops; Esc cancels):
   - Hold Ctrl+Space         → smart dictation
   - Hold Ctrl+Shift+Space   → literal
   - Hold Ctrl+Alt+Space     → assistant

Launcher hint: {parent}
Working directory: {Path.cwd()}
"""


def ensure_setup_hints(*, open_settings: bool = False) -> None:
    """Optionally open mic privacy settings; always safe to call."""
    if open_settings:
        open_privacy_settings()
