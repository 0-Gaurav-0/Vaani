"""Best-effort, privacy-safe audible feedback."""
from __future__ import annotations
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

SOUNDS = {
    "start": "/usr/share/sounds/freedesktop/stereo/message.oga",
    "stop": "/usr/share/sounds/freedesktop/stereo/button-pressed.oga",
    "success": "/usr/share/sounds/freedesktop/stereo/complete.oga",
    "busy": "/usr/share/sounds/freedesktop/stereo/dialog-warning.oga",
    "failure": "/usr/share/sounds/freedesktop/stereo/dialog-error.oga",
}
CATEGORIES = {"key", "mic", "Groq", "quota", "cleanup", "target", "paste", "shortcut"}

class Feedback:
    def __init__(self, *, paplay: str = "paplay", env: Mapping[str, str] | None = None,
                 runner: Any = subprocess.run, beeper: Any | None = None):
        self.paplay, self.runner, self.beeper = paplay, runner, beeper
        self.env = {"PATH": "/usr/bin:/bin"}; self.indicator = None
        if env:
            self.env.update({k: v for k, v in env.items() if k in {"PATH", "LANG", "LC_ALL"}})

    def play(self, cue: str) -> bool:
        if cue == "start" and self.indicator is None:
            try: self.indicator = subprocess.Popen([sys.executable, "-m", "vaani.indicator"], env=os.environ.copy(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception: self.indicator = None
        elif cue in {"success", "failure", "busy", "paste", "processing"} and self.indicator is not None:
            try: self.indicator.terminate()
            except Exception: pass
            self.indicator = None
        path = SOUNDS.get(cue)
        try:
            if path and Path(path).is_file():
                result = self.runner([self.paplay, path], shell=False, check=False,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=self.env)
                if getattr(result, "returncode", 1) == 0: return True
        except Exception: pass
        try:
            result = self.runner(["canberra-gtk-play", "-i", "complete"], shell=False, check=False,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=self.env)
            if getattr(result, "returncode", 1) == 0: return True
        except Exception: pass
        try:
            if self.beeper: self.beeper(); return True
            from gi.repository import Gdk
            Gdk.beep(); return True
        except Exception: return False

    def notify(self, category: str, message: str = "") -> None:
        if category not in CATEGORIES: category = "paste"
        text = message if message and len(message) < 160 else {
            "key":"API key required", "mic":"Microphone unavailable", "Groq":"Transcription unavailable",
            "quota":"Groq quota or rate limit reached", "cleanup":"Cleanup unavailable",
            "target":"Target changed; text copied only", "paste":"Paste unavailable", "shortcut":"Shortcut unavailable",
        }[category]
        try:
            self.runner(["notify-send", "Vaani", text], shell=False, check=False,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=self.env)
        except Exception:
            try:
                from gi.repository import Gdk
                Gdk.beep()
            except Exception: pass

    def feedback(self, status: str) -> bool:
        return self.play("failure" if status == "failed" else "success")


FeedbackPlayer = Feedback

def play_feedback(cue: str, *, player: Feedback | None = None) -> bool:
    return (player or Feedback()).play(cue)
