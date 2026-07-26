"""macOS notifications (osascript) and optional afplay cues."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Callable, Mapping

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


class MacFeedback:
    def __init__(
        self,
        *,
        runner: Callable[..., Any] = subprocess.run,
        env: Mapping[str, str] | None = None,
    ):
        self.runner = runner
        self.env = dict(env or {})

    def play(self, cue: str) -> bool:
        path = SOUNDS.get(cue)
        if not path or not Path(path).is_file():
            return False
        try:
            result = self.runner(
                ["afplay", path],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return getattr(result, "returncode", 1) == 0
        except Exception:
            return False

    def notify(self, category: str, message: str = "") -> None:
        if category not in CATEGORIES:
            category = "paste"
        text = message if message and len(message) < 160 else _DEFAULT_MESSAGES[category]
        # Escape for AppleScript string literal.
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
