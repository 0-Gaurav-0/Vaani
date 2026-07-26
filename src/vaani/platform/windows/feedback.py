"""Windows notification, optional sound cues, and recording pill lifecycle."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Mapping

from ...indicator_protocol import clear_phase, resolve_phase_path, write_phase

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

_DISMISS_CUES = frozenset({"success", "failure", "busy", "paste", "stop"})


def _powershell_balloon(title: str, body: str, runner: Any) -> bool:
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
        amplitude_path: str | os.PathLike[str] | None = None,
        control_path: str | os.PathLike[str] | None = None,
        popen: Callable[..., Any] = subprocess.Popen,
    ):
        self.runner = runner
        self.popen = popen
        self.beeper = beeper
        self.printer = printer or (lambda text: print(text, file=sys.stderr))
        self.env = dict(env or {})
        self.amplitude_path = (
            str(amplitude_path)
            if amplitude_path is not None
            else os.environ.get("VAANI_AMPLITUDE_PATH")
        )
        self.control_path = (
            str(control_path)
            if control_path is not None
            else os.environ.get("VAANI_INDICATOR_CONTROL")
        )
        self.phase_path = str(
            resolve_phase_path(
                cache_dir=Path(self.amplitude_path).parent
                if self.amplitude_path
                else None
            )
        )
        self.indicator = None

    def _spawn_indicator(self) -> None:
        if self.indicator is not None:
            if getattr(self.indicator, "poll", lambda: None)() is None:
                return
            self.indicator = None
        env = os.environ.copy()
        env.update(self.env)
        if self.amplitude_path:
            env["VAANI_AMPLITUDE_PATH"] = self.amplitude_path
        if self.control_path:
            env["VAANI_INDICATOR_CONTROL"] = self.control_path
        env["VAANI_INDICATOR_PHASE"] = self.phase_path
        try:
            write_phase(self.phase_path, "recording")
        except Exception:
            pass
        try:
            self.indicator = self.popen(
                [sys.executable, "-m", "vaani.platform.windows.indicator_app"],
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            self.indicator = None

    def _stop_indicator(self) -> None:
        try:
            clear_phase(self.phase_path)
        except Exception:
            pass
        if self.indicator is None:
            return
        try:
            self.indicator.terminate()
        except Exception:
            pass
        self.indicator = None

    def play(self, cue: str) -> bool:
        if cue == "start":
            self._spawn_indicator()
        elif cue == "processing":
            try:
                write_phase(self.phase_path, "processing")
            except Exception:
                pass
            self.notify("paste", "Transcribing…")
        elif cue in _DISMISS_CUES:
            self._stop_indicator()
        try:
            if self.beeper is not None:
                self.beeper()
                return True
            import winsound

            winsound.MessageBeep()
            return True
        except Exception:
            return cue in {"start", "processing"}

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


Feedback = WindowsFeedback
