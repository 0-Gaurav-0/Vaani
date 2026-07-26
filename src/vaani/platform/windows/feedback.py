"""Windows notification and optional sound cues."""
from __future__ import annotations

import subprocess
import sys
from typing import Any, Callable, Mapping

CATEGORIES = {"key", "mic", "Groq", "quota", "cleanup", "target", "paste", "shortcut"}

_DEFAULT_MESSAGES = {
    "key": "API key required",
    "mic": "Microphone unavailable",
    "Groq": "Transcription unavailable",
    "quota": "Groq quota or rate limit reached",
    "cleanup": "Cleanup unavailable",
    "target": "Target changed; text copied only",
    "paste": "Paste unavailable",
    "shortcut": "Shortcut unavailable",
}


def _powershell_balloon(title: str, body: str, runner: Any) -> bool:
    # Escape single quotes for PowerShell single-quoted strings.
    safe_title = title.replace("'", "''")
    safe_body = body.replace("'", "''")
    script = (
        "[void][reflection.assembly]::LoadWithPartialName('System.Windows.Forms');"
        "[void][reflection.assembly]::LoadWithPartialName('System.Drawing');"
        "$n = New-Object System.Windows.Forms.NotifyIcon;"
        "$n.Icon = [System.Drawing.SystemIcons]::Information;"
        "$n.Visible = $true;"
        f"$n.ShowBalloonTip(2500, '{safe_title}', '{safe_body}', "
        "[System.Windows.Forms.ToolTipIcon]::None);"
        "Start-Sleep -Milliseconds 2600;"
        "$n.Dispose()"
    )
    try:
        result = runner(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=8,
        )
        return getattr(result, "returncode", 1) == 0
    except Exception:
        return False


class WindowsFeedback:
    """Toast/balloon notifications with console + winsound fallbacks."""

    def __init__(
        self,
        *,
        runner: Any = subprocess.run,
        beeper: Callable[[], None] | None = None,
        printer: Callable[[str], None] | None = None,
        env: Mapping[str, str] | None = None,
    ):
        self.runner = runner
        self.beeper = beeper
        self.printer = printer or (lambda text: print(text, file=sys.stderr))
        self.env = env

    def play(self, cue: str) -> bool:
        try:
            if self.beeper is not None:
                self.beeper()
                return True
            import winsound

            winsound.MessageBeep()
            return True
        except Exception:
            return False

    def notify(self, category: str, message: str = "") -> None:
        if category not in CATEGORIES:
            category = "paste"
        text = message if message and len(message) < 160 else _DEFAULT_MESSAGES[category]
        if _powershell_balloon("Vaani", text, self.runner):
            return
        try:
            self.printer(f"Vaani [{category}]: {text}")
        except Exception:
            pass
        try:
            self.play("failure" if category in {"mic", "Groq", "paste", "shortcut"} else "success")
        except Exception:
            pass
