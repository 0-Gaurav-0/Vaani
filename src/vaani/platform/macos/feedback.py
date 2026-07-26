"""macOS notifications (osascript), afplay cues, and recording pill lifecycle."""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Mapping

from ...indicator_protocol import clear_phase, resolve_phase_path, write_phase

SOUNDS = {
    "start": "/System/Library/Sounds/Tink.aiff",
    "stop": "/System/Library/Sounds/Pop.aiff",
    "success": "/System/Library/Sounds/Glass.aiff",
    "busy": "/System/Library/Sounds/Funk.aiff",
    "failure": "/System/Library/Sounds/Basso.aiff",
    "processing": "/System/Library/Sounds/Pop.aiff",
    "paste": "/System/Library/Sounds/Pop.aiff",
}

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
_LOG = logging.getLogger("vaani")


class MacFeedback:
    def __init__(
        self,
        *,
        runner: Callable[..., Any] = subprocess.run,
        env: Mapping[str, str] | None = None,
        amplitude_path: str | os.PathLike[str] | None = None,
        control_path: str | os.PathLike[str] | None = None,
        popen: Callable[..., Any] = subprocess.Popen,
        log_dir: str | os.PathLike[str] | None = None,
    ):
        self.runner = runner
        self.popen = popen
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
        self.log_dir = Path(
            log_dir
            or os.environ.get(
                "VAANI_LOG_DIR",
                Path.home() / "Library" / "Application Support" / "Vaani" / "logs",
            )
        )
        self.indicator = None

    def _spawn_indicator(self) -> None:
        if self.indicator is not None:
            if self.indicator.poll() is None:
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
                [sys.executable, "-m", "vaani.platform.macos.indicator_app"],
                env=env,
                stdout=err_fh,
                stderr=err_fh,
                start_new_session=True,
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
        if self.indicator is None:
            try:
                clear_phase(self.phase_path)
            except Exception:
                pass
            return
        proc = self.indicator
        self.indicator = None
        try:
            clear_phase(self.phase_path)
        except Exception:
            pass
        try:
            if getattr(proc, "pid", None):
                os.killpg(proc.pid, signal.SIGTERM)
            else:
                proc.terminate()
        except Exception:
            try:
                proc.terminate()
            except Exception:
                pass

    def play(self, cue: str) -> bool:
        if cue == "start":
            self._spawn_indicator()
        elif cue == "processing":
            # Keep pill visible; switch to processing animation.
            try:
                write_phase(self.phase_path, "processing")
            except Exception:
                pass
            self.notify("paste", "Transcribing…")
        elif cue in _DISMISS_CUES:
            self._stop_indicator()
        path = SOUNDS.get(cue)
        if not path or not Path(path).is_file():
            return cue in {"start", "processing"}
        try:
            self.popen(
                ["afplay", path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except Exception:
            return False

    def notify(self, category: str, message: str = "") -> None:
        if category not in CATEGORIES:
            category = "paste"
        text = message if message and len(message) < 160 else _DEFAULT_MESSAGES[category]
        safe = text.replace("\\", "\\\\").replace('"', '\\"')
        script = f'display notification "{safe}" with title "Vaani"'
        try:
            self.runner(
                ["osascript", "-e", script],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass

    def feedback(self, status: str) -> bool:
        return self.play("failure" if status == "failed" else "success")


Feedback = MacFeedback
FeedbackPlayer = MacFeedback
