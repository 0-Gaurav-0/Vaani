"""Meeting shortcut handling inside the Vaani daemon (no heavy imports).

TrackPoint + R starts a meeting; TrackPoint + R R stops it; a single R while
recording is ignored so a stray tap can't end a meeting. After stop (or the
4 h cap) the worker is spawned detached via ``systemd-run --user``.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable

from .job import find_jobs
from .recorder import MAX_MEETING_S, WARN_BEFORE_MAX_S, MeetingRecorder
from .util import notify

LOGGER = logging.getLogger("vaani")

RETRY_CHECK_S = 30 * 60


class MeetingControl:
    def __init__(
        self,
        root: Path,
        *,
        recorder: MeetingRecorder | None = None,
        feedback: Callable[[str], None] | None = None,
        spawn: Callable[[], None] | None = None,
        logger: logging.Logger = LOGGER,
        max_s: float = MAX_MEETING_S,
    ):
        self.root = Path(root)
        self.recorder = recorder or MeetingRecorder(self.root, logger=logger)
        self._feedback = feedback or (lambda cue: None)
        self._spawn = spawn or self._spawn_worker
        self._logger = logger
        self._lock = threading.Lock()
        self._token = 0
        self.max_s = max_s
        self._stop = threading.Event()

    @property
    def active(self) -> bool:
        return self.recorder.active

    def shortcut(self, taps: int) -> bool:
        """Handle TrackPoint+R (taps=1) / TrackPoint+R R (taps>=2)."""
        with self._lock:
            if not self.recorder.active:
                return self._start()
            if taps >= 2:
                return self._finish("shortcut")
        self._logger.info("event=meeting_single_tap_ignored")
        self._feedback("busy")
        return False

    def _start(self) -> bool:
        try:
            job = self.recorder.start()
        except Exception as exc:
            self._logger.error("event=meeting_start_failed detail=%s", exc)
            self._feedback("failure")
            notify("Meeting recording failed to start", str(exc)[:200])
            return False
        self._token += 1
        threading.Thread(target=self._cap_guard, args=(self._token,), daemon=True, name="vaani-meeting-cap").start()
        self._feedback("start")
        # Stays on screen until dismissed: the visible "recording" signal.
        notify("● Meeting recording", f"TrackPoint + R R to stop.\n{job.dir}", sticky=True)
        return True

    def _finish(self, reason: str) -> bool:
        job = self.recorder.stop()
        self._token += 1
        if job is None:
            return False
        self._logger.info("event=meeting_finish reason=%s", reason)
        self._feedback("processing")
        notify("Meeting recording stopped", "Transcribing in the background; you'll get a notification.")
        self._spawn()
        return True

    def _cap_guard(self, token: int) -> None:
        remaining = self.max_s - self.recorder.elapsed
        if remaining > WARN_BEFORE_MAX_S and self._stop.wait(remaining - WARN_BEFORE_MAX_S):
            return
        with self._lock:
            if token != self._token:
                return
        self._feedback("warn")
        notify("Meeting will stop in 5 minutes", "4 hour limit. Start a new meeting to continue.")
        if self._stop.wait(max(0.0, self.max_s - self.recorder.elapsed)):
            return
        with self._lock:
            if token == self._token and self.recorder.active:
                self._finish("cap")

    def startup(self) -> None:
        """Adopt a meeting that survived a restart; queue work left by a crash."""
        closed = self.recorder.adopt()
        if self.recorder.active:
            self._token += 1
            threading.Thread(target=self._cap_guard, args=(self._token,), daemon=True).start()
        if closed or any(job.due() for job in find_jobs(self.root)):
            self._spawn()
        threading.Thread(target=self._retry_loop, daemon=True, name="vaani-meeting-retry").start()

    def _retry_loop(self) -> None:
        while not self._stop.wait(RETRY_CHECK_S):
            try:
                if any(job.due() for job in find_jobs(self.root)):
                    self._spawn()
            except Exception:
                pass

    def shutdown(self) -> None:
        # Deliberately leaves a running meeting recording; the next start adopts it.
        self._stop.set()

    def _spawn_worker(self) -> None:
        argv = [sys.executable, "-m", "vaani.meeting.worker", str(self.root)]
        if shutil.which("systemd-run"):
            argv = ["systemd-run", "--user", "--collect", "--quiet", "--nice=10",
                    "--unit=vaani-meeting-worker", *argv]
        try:
            subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            self._logger.info("event=meeting_worker_spawned")
        except OSError as exc:
            self._logger.error("event=meeting_worker_spawn_failed detail=%s", exc)
