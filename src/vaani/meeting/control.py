"""Meeting shortcut + pill handling inside the Vaani daemon (no heavy imports).

TrackPoint + R starts a meeting; TrackPoint + R R stops it; a single R while
recording is ignored so a stray tap can't end a meeting. The meeting pill
(its own process) shows a live waveform and recorded time with ⏸/▶ and ■;
it disappears the moment the meeting stops — processing runs in a detached
worker, so a new meeting can start right away. The 4 h cap counts recorded
time only (pauses excluded); hitting it stops *and processes*.
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
TICK_S = 0.05  # pill waveform + button poll


class MeetingControl:
    def __init__(
        self,
        root: Path,
        *,
        recorder: MeetingRecorder | None = None,
        pill=None,
        sound: Callable[[str], None] | None = None,
        spawn: Callable[[], None] | None = None,
        logger: logging.Logger = LOGGER,
        max_s: float = MAX_MEETING_S,
    ):
        self.root = Path(root)
        self.recorder = recorder or MeetingRecorder(self.root, logger=logger)
        self.pill = pill
        self._sound = sound or (lambda cue: None)
        self._spawn = spawn or self._spawn_worker
        self._logger = logger
        self._lock = threading.RLock()
        self._token = 0
        self.max_s = max_s
        self._warned = False
        self._stop = threading.Event()

    @property
    def active(self) -> bool:
        return self.recorder.active

    # ---- user actions ------------------------------------------------------

    def shortcut(self, taps: int) -> bool:
        """Handle TrackPoint+R (taps=1) / TrackPoint+R R (taps>=2)."""
        with self._lock:
            if not self.recorder.active:
                return self._start()
            if taps >= 2:
                return self._finish("shortcut")
        self._logger.info("event=meeting_single_tap_ignored")
        self._sound("busy")
        return False

    def pause(self) -> bool:
        with self._lock:
            if not self.recorder.pause():
                return False
            self._sync_pill()
        self._sound("processing")
        return True

    def resume(self) -> bool:
        with self._lock:
            try:
                ok = self.recorder.resume()
            except Exception as exc:
                self._logger.error("event=meeting_resume_failed detail=%s", exc)
                self._sound("failure")
                notify("Meeting resume failed", f"{exc}"[:200] + "\nThe part recorded so far is kept.")
                self._finish("resume_failed")
                return False
            if not ok:
                return False
            self._sync_pill()
        self._sound("start")
        return True

    # ---- lifecycle ---------------------------------------------------------

    def _start(self) -> bool:
        try:
            job = self.recorder.start()
        except Exception as exc:
            self._logger.error("event=meeting_start_failed detail=%s", exc)
            self._sound("failure")
            notify("Meeting recording failed to start", str(exc)[:200])
            return False
        self._sound("start")
        self._begin_session()
        notify("Meeting recording", f"TrackPoint + R R or ■ on the red pill to stop.\n{job.dir}")
        return True

    def _begin_session(self) -> None:
        self._token += 1
        self._warned = False
        if self.pill is not None:
            job = self.recorder.job
            try:
                self.pill.show(
                    started=job.started,
                    max_s=self.max_s,
                    warn_s=max(0.0, self.max_s - WARN_BEFORE_MAX_S),
                    paused_total=job.paused_total,
                    paused_since=job.paused_since,
                )
            except Exception as exc:
                self._logger.warning("event=meeting_pill_failed detail=%s", type(exc).__name__)
        threading.Thread(target=self._loop, args=(self._token,), daemon=True, name="vaani-meeting").start()

    def _sync_pill(self) -> None:
        job = self.recorder.job
        if self.pill is None or job is None:
            return
        try:
            self.pill.update(
                started=job.started,
                max_s=self.max_s,
                warn_s=max(0.0, self.max_s - WARN_BEFORE_MAX_S),
                paused_total=job.paused_total,
                paused_since=job.paused_since,
            )
        except Exception:
            pass

    def _finish(self, reason: str) -> bool:
        with self._lock:
            self._token += 1
            if self.pill is not None:
                self.pill.hide()  # gone now; processing is background work
            job = self.recorder.stop()
        if job is None:
            return False
        self._logger.info("event=meeting_finish reason=%s", reason)
        self._sound("stop")
        notify("Meeting saved", "Transcribing in the background; you'll get a notification.")
        self._spawn()
        return True

    def _loop(self, token: int) -> None:
        ticks = 0
        while not self._stop.wait(TICK_S):
            with self._lock:
                if token != self._token or not self.recorder.active:
                    return
            ticks += 1
            if self.pill is not None:
                self.pill.level(self.recorder.level())
                command = self.pill.poll_command()
                if command == "stop":
                    self._finish("pill")
                    return
                if command == "pause":
                    self.pause()
                elif command == "resume":
                    self.resume()
            if ticks % 20 == 0:
                recorded = self.recorder.elapsed
                if not self._warned and recorded >= self.max_s - WARN_BEFORE_MAX_S:
                    self._warned = True
                    self._sound("warn")
                    notify("Meeting will stop in 5 minutes", "4 hour limit. Start a new meeting to continue.")
                if recorded >= self.max_s:
                    self._finish("cap")
                    return

    def startup(self) -> None:
        """Adopt a meeting that survived a restart; queue work left by a crash."""
        closed = self.recorder.adopt()
        if self.recorder.active:
            self._begin_session()
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
        if self.pill is not None:
            self.pill.hide()

    def _spawn_worker(self) -> None:
        argv = [sys.executable, "-m", "vaani.meeting.worker", str(self.root)]
        if shutil.which("systemd-run"):
            # No fixed unit name: back-to-back meetings each get a worker;
            # the per-job flock stops two workers doing the same job.
            argv = ["systemd-run", "--user", "--collect", "--quiet", "--nice=10", *argv]
        try:
            subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            self._logger.info("event=meeting_worker_spawned")
        except OSError as exc:
            self._logger.error("event=meeting_worker_spawn_failed detail=%s", exc)
