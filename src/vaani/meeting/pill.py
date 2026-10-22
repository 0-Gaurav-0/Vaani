"""The meeting pill: a second indicator process with its own files.

Same pill app as dictation (``VAANI_INDICATOR_KIND=meeting``), so both can be
on screen at once and never share phase/amplitude/control state. Shown while a
meeting records (live mic waveform, recorded-time timer, ⏸/▶ and ■), killed
the moment the meeting stops — processing runs in the background unseen.
"""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
from pathlib import Path

from ..indicator_protocol import clear_command, read_command, write_phase, write_session

LOGGER = logging.getLogger("vaani")


class MeetingPill:
    def __init__(self, cache_dir: Path, log_dir: Path, *, popen=subprocess.Popen, logger: logging.Logger = LOGGER):
        base = Path(cache_dir) / "meeting"
        self.amplitude = base / "amplitude"
        self.phase = base / "phase"
        self.control = base / "control.json"
        self.session = base / "session.json"
        self.answer = base / "answer.json"
        self.log_dir = Path(log_dir)
        self._popen = popen
        self._logger = logger
        self._proc = None

    def show(self, *, started: float, max_s: float, warn_s: float, paused_total: float, paused_since: float) -> None:
        self.update(started=started, max_s=max_s, warn_s=warn_s, paused_total=paused_total, paused_since=paused_since)
        write_phase(self.phase, "recording")
        clear_command(self.control)
        if self._proc is not None and self._proc.poll() is None:
            return
        env = os.environ.copy()
        env.update(
            VAANI_INDICATOR_KIND="meeting",
            VAANI_AMPLITUDE_PATH=str(self.amplitude),
            VAANI_INDICATOR_CONTROL=str(self.control),
            VAANI_INDICATOR_PHASE=str(self.phase),
            VAANI_INDICATOR_ANSWER=str(self.answer),
            VAANI_INDICATOR_SESSION=str(self.session),
            VAANI_DAEMON_PID=str(os.getpid()),
        )
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            err = open(self.log_dir / "indicator-meeting.err", "ab", buffering=0)
        except OSError:
            err = subprocess.DEVNULL
        try:
            self._proc = self._popen(
                [sys.executable, "-m", "vaani.platform.linux.indicator_app"],
                env=env,
                stdout=err,
                stderr=err,
                start_new_session=True,
            )
        except Exception as exc:
            self._logger.warning("event=meeting_pill_spawn_failed detail=%s", type(exc).__name__)
            self._proc = None

    def update(self, *, started: float, max_s: float, warn_s: float, paused_total: float, paused_since: float) -> None:
        write_session(
            self.session,
            mode="meeting",
            handsfree=True,
            started=started,
            max_s=max_s,
            warn_s=warn_s,
            paused_total=paused_total,
            paused_since=paused_since,
        )

    def level(self, value: float) -> None:
        try:
            tmp = self.amplitude.with_suffix(".tmp")
            tmp.write_text(f"{value:.4f}", encoding="utf-8")
            os.replace(tmp, self.amplitude)
        except OSError:
            pass

    def poll_command(self) -> str | None:
        command = read_command(self.control)
        if command is not None:
            clear_command(self.control)
        return command

    def hide(self) -> None:
        try:
            write_phase(self.phase, "idle")
        except OSError:
            pass
        proc, self._proc = self._proc, None
        if proc is not None and proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except OSError:
                try:
                    proc.terminate()
                except OSError:
                    pass
