"""Linux notifications (notify-send), paplay cues, and recording pill lifecycle."""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Mapping

from ...indicator_protocol import (
    clear_answer,
    clear_phase,
    resolve_answer_path,
    resolve_phase_path,
    resolve_session_path,
    write_answer,
    write_phase,
    write_session,
)

SOUNDS = {
    "start": "/usr/share/sounds/freedesktop/stereo/message.oga",
    "stop": "/usr/share/sounds/freedesktop/stereo/button-pressed.oga",
    # success/paste intentionally silent — paste already confirms visually.
    "success": None,
    "busy": "/usr/share/sounds/freedesktop/stereo/dialog-warning.oga",
    "failure": "/usr/share/sounds/freedesktop/stereo/dialog-error.oga",
    "processing": "/usr/share/sounds/freedesktop/stereo/button-pressed.oga",
    "paste": None,
    # 1 minute before the recording cap.
    "warn": "/usr/share/sounds/freedesktop/stereo/dialog-information.oga",
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
# Answer phase has its own auto-dismiss in the indicator; no answer cue here.
_DISMISS_CUES = frozenset({"success", "failure", "busy", "paste", "stop"})
_SILENT_CUES = frozenset({"success", "paste"})
_SESSION_ENV_KEYS = frozenset(
    {
        "PATH",
        "LANG",
        "LC_ALL",
        "DISPLAY",
        "DBUS_SESSION_BUS_ADDRESS",
        "XDG_RUNTIME_DIR",
        "XAUTHORITY",
        "HOME",
    }
)
_LOG = logging.getLogger("vaani")


class LinuxFeedback:
    def __init__(
        self,
        *,
        paplay: str = "paplay",
        env: Mapping[str, str] | None = None,
        runner: Any = subprocess.run,
        beeper: Any | None = None,
        amplitude_path: str | os.PathLike[str] | None = None,
        control_path: str | os.PathLike[str] | None = None,
        popen: Callable[..., Any] = subprocess.Popen,
        log_dir: str | os.PathLike[str] | None = None,
        persistent_indicator: bool = True,
        sound_async: bool | None = None,
    ):
        self.paplay = paplay
        # Keep one pill process alive (hidden when idle) so the next press shows
        # it instantly instead of paying ~1s python + GTK4 startup each time.
        self.persistent_indicator = persistent_indicator
        # Cue sounds must never block the hotkey / stop path. Injected runners
        # (tests) stay synchronous so assertions are deterministic.
        self.sound_async = (runner is subprocess.run) if sound_async is None else sound_async
        self.runner = runner
        self.beeper = beeper
        self.popen = popen
        self.env = {"PATH": "/usr/bin:/bin"}
        for key in (
            "DISPLAY",
            "DBUS_SESSION_BUS_ADDRESS",
            "XDG_RUNTIME_DIR",
            "XAUTHORITY",
            "HOME",
        ):
            value = os.environ.get(key)
            if value:
                self.env[key] = value
        if env:
            self.env.update(
                {k: v for k, v in env.items() if k in _SESSION_ENV_KEYS}
            )
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
        cache_dir = (
            Path(self.amplitude_path).parent if self.amplitude_path else None
        )
        self.phase_path = str(resolve_phase_path(cache_dir=cache_dir))
        self.answer_path = str(resolve_answer_path(cache_dir=cache_dir))
        self.session_path = str(resolve_session_path(cache_dir=cache_dir))
        self.log_dir = Path(
            log_dir
            or os.environ.get("VAANI_LOG_DIR", Path.home() / ".local" / "state" / "vaani" / "logs")
        )
        self.indicator = None

    def _indicator_alive(self) -> bool:
        proc = self.indicator
        return proc is not None and getattr(proc, "poll", lambda: None)() is None

    def prewarm(self) -> None:
        """Start the hidden pill at daemon startup so the first press is instant."""
        if not self.persistent_indicator or self._indicator_alive():
            return
        self._spawn_indicator(initial_phase="idle")

    def shutdown(self) -> None:
        """Kill the persistent pill (daemon exit)."""
        self._kill_indicator()

    close = shutdown

    def _spawn_indicator(self, initial_phase: str = "recording") -> None:
        if self.indicator is not None:
            if getattr(self.indicator, "poll", lambda: None)() is None:
                # Reuse the live answer card — flip it back to recording.
                try:
                    write_phase(self.phase_path, "recording")
                except Exception:
                    pass
                try:
                    clear_answer(self.answer_path)
                except Exception:
                    pass
                return
            self.indicator = None
        env = os.environ.copy()
        env.update(self.env)
        if self.amplitude_path:
            env["VAANI_AMPLITUDE_PATH"] = self.amplitude_path
        if self.control_path:
            env["VAANI_INDICATOR_CONTROL"] = self.control_path
        env["VAANI_INDICATOR_PHASE"] = self.phase_path
        env["VAANI_INDICATOR_ANSWER"] = self.answer_path
        env["VAANI_INDICATOR_SESSION"] = self.session_path
        env["VAANI_DAEMON_PID"] = str(os.getpid())
        try:
            write_phase(self.phase_path, initial_phase)
        except Exception:
            pass
        try:
            clear_answer(self.answer_path)
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
                [sys.executable, "-m", "vaani.platform.linux.indicator_app"],
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
        if self.persistent_indicator and self._indicator_alive():
            # Hide, don't kill — the next press reuses the warm process.
            try:
                write_phase(self.phase_path, "idle")
            except Exception:
                pass
            try:
                clear_answer(self.answer_path)
            except Exception:
                pass
            return
        self._kill_indicator()

    def _kill_indicator(self) -> None:
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

    def set_session(
        self, *, mode: str, handsfree: bool, started: float, max_s: float, warn_s: float
    ) -> None:
        """Tell the pill the mode, hands-free state and timer origin."""
        try:
            write_session(
                self.session_path,
                mode=mode,
                handsfree=handsfree,
                started=started,
                max_s=max_s,
                warn_s=warn_s,
            )
        except Exception:
            pass

    def show_answer(self, question: str, answer: str) -> None:
        """Expand the live pill with Q&A. Does not stop the indicator or toast."""
        try:
            write_answer(self.answer_path, question, answer)
            write_phase(self.phase_path, "answer")
        except Exception:
            pass

    def show_clarify(
        self, question: str, options: list[str] | tuple[str, ...]
    ) -> None:
        """Expand the pill with numbered choices the user can click or speak."""
        labels = [str(item) for item in list(options)[:5]]
        body = "\n".join(f"{i}. {label}" for i, label in enumerate(labels, start=1))
        try:
            write_answer(self.answer_path, question, body, options=labels)
            write_phase(self.phase_path, "answer")
        except Exception:
            pass

    def show_confirm(self, question: str, action: str, seconds: float) -> None:
        """Action preview card: what was heard, what will run, countdown + buttons.

        Option 0 = run now, option 1 = cancel (clicks come back as option_N).
        """
        try:
            write_answer(
                self.answer_path,
                # Plain text: the pill's cairo font has no arrows/check glyphs.
                f"Heard: “{question}”\n{action}",
                "Do it now\nCancel",
                options=["Do it now", "Cancel"],
                countdown_s=seconds,
            )
            write_phase(self.phase_path, "answer")
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
        elif cue in _DISMISS_CUES:
            self._stop_indicator()

        if cue in _SILENT_CUES:
            return True
        if self.sound_async:
            threading.Thread(
                target=self._play_sound, args=(cue,), daemon=True, name="vaani-cue"
            ).start()
            return True
        return self._play_sound(cue)

    def _play_sound(self, cue: str) -> bool:
        path = SOUNDS.get(cue)
        try:
            if path and Path(path).is_file():
                result = self.runner(
                    [self.paplay, path],
                    shell=False,
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env=self.env,
                )
                if getattr(result, "returncode", 1) == 0:
                    return True
        except Exception:
            pass
        try:
            result = self.runner(
                ["canberra-gtk-play", "-i", "complete"],
                shell=False,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=self.env,
            )
            if getattr(result, "returncode", 1) == 0:
                return True
        except Exception:
            pass
        try:
            if self.beeper:
                self.beeper()
                return True
            from gi.repository import Gdk

            Gdk.beep()
            return True
        except Exception:
            return cue in {"start", "processing"}

    def notify(self, category: str, message: str = "") -> None:
        if category not in CATEGORIES:
            category = "paste"
        text = (
            message
            if message and len(message) < 160
            else _DEFAULT_MESSAGES[category]
        )
        try:
            self.runner(
                ["notify-send", "Vaani", text],
                shell=False,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=self.env,
            )
        except Exception:
            try:
                from gi.repository import Gdk

                Gdk.beep()
            except Exception:
                pass

    def feedback(self, status: str) -> bool:
        return self.play("failure" if status == "failed" else "success")


Feedback = LinuxFeedback
FeedbackPlayer = LinuxFeedback
