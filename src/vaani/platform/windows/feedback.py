"""Windows notification, sound cues, and recording pill lifecycle."""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
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

# Dismiss the pill — NOT processing (pill stays visible while Groq works).
_DISMISS_CUES = frozenset({"success", "failure", "busy", "paste", "stop"})

# (frequency_hz, duration_ms) for winsound.Beep; distinct per cue when available.
_CUE_BEEPS: dict[str, tuple[int, int]] = {
    "start": (880, 70),
    "processing": (660, 50),
    "success": (1040, 90),
    "failure": (420, 160),
    "busy": (520, 80),
    "paste": (780, 55),
    "stop": (700, 55),
}

_LOG = logging.getLogger("vaani")


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
        beeper: Callable[[str], None] | Callable[[], None] | None = None,
        printer: Callable[[str], None] | None = None,
        env: Mapping[str, str] | None = None,
        amplitude_path: str | os.PathLike[str] | None = None,
        control_path: str | os.PathLike[str] | None = None,
        popen: Callable[..., Any] = subprocess.Popen,
        log_dir: str | os.PathLike[str] | None = None,
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
        local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        self.log_dir = Path(
            log_dir
            or os.environ.get("VAANI_LOG_DIR", local / "Vaani" / "logs")
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
            self.log_dir.mkdir(parents=True, exist_ok=True)
            err_path = self.log_dir / "indicator.err"
            err_fh = open(err_path, "ab", buffering=0)
        except Exception:
            err_fh = subprocess.DEVNULL
        try:
            self.indicator = self.popen(
                [sys.executable, "-m", "vaani.platform.windows.indicator_app"],
                env=env,
                stdout=err_fh,
                stderr=err_fh,
            )
            _LOG.info(
                "event=indicator_spawned pid=%s exe=%s",
                getattr(self.indicator, "pid", None),
                sys.executable,
            )
        except Exception as exc:
            _LOG.warning("event=indicator_spawn_failed detail=%s", type(exc).__name__)
            self.indicator = None
            try:
                if err_fh is not subprocess.DEVNULL:
                    err_fh.close()
            except Exception:
                pass

    def _stop_indicator(self) -> None:
        try:
            clear_phase(self.phase_path)
        except Exception:
            pass
        if self.indicator is None:
            return
        proc = self.indicator
        self.indicator = None
        # On Windows the launcher can create a second Python child through the
        # environment shim. Terminating only the parent leaves the visible Tk
        # pill orphaned, so terminate the whole process tree by PID.
        if os.name == "nt" and getattr(proc, "pid", None):
            try:
                subprocess.run(
                    ["taskkill.exe", "/PID", str(proc.pid), "/T", "/F"],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=3,
                )
                return
            except Exception:
                pass
        try:
            proc.terminate()
        except Exception:
            pass

    def _play_sound(self, cue: str) -> bool:
        if self.beeper is not None:
            try:
                # Support both beeper() and beeper(cue) injectors.
                try:
                    self.beeper(cue)  # type: ignore[misc]
                except TypeError:
                    self.beeper()  # type: ignore[misc]
                return True
            except Exception:
                return False
        beep = _CUE_BEEPS.get(cue)

        def _beep() -> None:
            try:
                import winsound

                if beep is not None:
                    winsound.Beep(beep[0], beep[1])
                else:
                    winsound.MessageBeep()
            except Exception:
                try:
                    import winsound

                    winsound.MessageBeep()
                except Exception:
                    pass

        try:
            threading.Thread(target=_beep, daemon=True).start()
            return True
        except Exception:
            return False

    def play(self, cue: str) -> bool:
        if cue == "start":
            self._spawn_indicator()
        elif cue == "processing":
            # Keep pill visible; switch to processing animation.
            try:
                write_phase(self.phase_path, "processing")
            except Exception:
                pass
        elif cue in _DISMISS_CUES:
            self._stop_indicator()
        sounded = self._play_sound(cue)
        return sounded or cue in {"start", "processing"}

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

    def feedback(self, status: str) -> bool:
        return self.play("failure" if status == "failed" else "success")


Feedback = WindowsFeedback
FeedbackPlayer = WindowsFeedback
