"""Two-track meeting capture: mic + laptop output (sink monitor).

Each track is a detached ``parec`` writing raw s16le 16 kHz mono straight to
disk, so a Vaani crash or restart does not stop the meeting recording; the
next Vaani adopts the still-running processes from ``job.json``. The
``--client-name`` marker keeps :func:`vaani.audio.reap_orphan_parec` (exact
argv match) away from these processes.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import signal
import subprocess
import time
from pathlib import Path

from ..audio import PAREC_LATENCY_ARG
from ..config import child_environment
from .job import RECORDED, RECORDING, MeetingJob, find_jobs

LOGGER = logging.getLogger("vaani")

CLIENT_MARKER = "--client-name=vaani-meeting"
TRACKS = {"mic": "@DEFAULT_SOURCE@", "system": "@DEFAULT_MONITOR@"}
RATE = 16000
MAX_MEETING_S = 4 * 3600
WARN_BEFORE_MAX_S = 300


def parec_argv(device: str, parec: str = "parec") -> list[str]:
    return [
        parec,
        f"--device={device}",
        "--rate=16000",
        "--channels=1",
        "--format=s16le",
        "--raw",
        PAREC_LATENCY_ARG,
        CLIENT_MARKER,
    ]


def scoped(argv: list[str]) -> list[str]:
    """Run in its own systemd user scope so killing Vaani's cgroup (crash,
    release restart, closing the terminal app it was started from) doesn't
    stop the meeting. ``--scope`` execs in place, so the PID is unchanged."""
    if shutil.which("systemd-run"):
        return ["systemd-run", "--user", "--scope", "--quiet", "--collect", *argv]
    return argv


def output_kind(run=subprocess.run) -> str:
    """'headphones' when the default sink plays to headphones/Bluetooth, else 'speaker'.

    On laptop speakers the mic already hears remote voices, so mixing in the
    monitor track would double them; prepare uses the mic alone then.
    """
    try:
        default = run(["pactl", "get-default-sink"], capture_output=True, text=True, timeout=3).stdout.strip()
        listing = run(["pactl", "list", "sinks"], capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return "speaker"
    if default.startswith("bluez"):
        return "headphones"
    for block in listing.split("Sink #"):
        if f"Name: {default}" in block:
            port = re.search(r"Active Port:\s*(.+)", block)
            if port and re.search(r"head", port.group(1), re.IGNORECASE):
                return "headphones"
    return "speaker"


def _alive_meeting_pid(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            return CLIENT_MARKER.encode() in fh.read()
    except OSError:
        return False


class MeetingRecorder:
    def __init__(self, root: Path, *, parec: str = "parec", logger: logging.Logger = LOGGER):
        self.root = Path(root)
        self.parec = parec
        self._logger = logger
        self.job: MeetingJob | None = None

    @property
    def active(self) -> bool:
        return self.job is not None

    @property
    def elapsed(self) -> float:
        return time.time() - self.job.started if self.job else 0.0

    def start(self) -> MeetingJob:
        if self.job is not None:
            raise RuntimeError("meeting already recording")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        stamp = time.strftime("%Y-%m-%d %H%M")
        directory = self.root / stamp
        n = 1
        while directory.exists():
            n += 1
            directory = self.root / f"{stamp} ({n})"
        (directory / "audio").mkdir(parents=True, mode=0o700)
        job = MeetingJob(directory, started=time.time(), output=output_kind())
        env = child_environment(None)
        procs: list[subprocess.Popen] = []
        try:
            for name, device in TRACKS.items():
                fd = os.open(directory / "audio" / f"{name}.raw", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as out:
                    procs.append(
                        subprocess.Popen(
                            scoped(parec_argv(device, self.parec)),
                            stdout=out,
                            stderr=subprocess.DEVNULL,
                            env=env,
                            start_new_session=True,  # survives a Vaani crash/restart
                        )
                    )
            time.sleep(0.5)  # systemd-run --scope registers, then execs parec
            dead = [p for p in procs if p.poll() not in (None, 0)]
            if dead:
                raise RuntimeError(f"parec exited {dead[0].returncode}")
        except Exception:
            for p in procs:
                _stop_pid(p.pid)
            raise
        job.pids = [p.pid for p in procs]
        job.save()
        self.job = job
        self._logger.info("event=meeting_start dir=%s output=%s", directory.name, job.output)
        return job

    def stop(self) -> MeetingJob | None:
        job, self.job = self.job, None
        if job is None:
            return None
        for pid in job.pids:
            _stop_pid(pid)
        job.advance(RECORDED, stopped=time.time(), pids=[])
        self._logger.info("event=meeting_stop dir=%s seconds=%.0f", job.dir.name, job.stopped - job.started)
        return job

    def adopt(self) -> list[MeetingJob]:
        """At startup: re-attach a meeting still recording; close dead ones.

        Returns jobs that were closed (now ``recorded``) and need processing.
        """
        closed: list[MeetingJob] = []
        for job in find_jobs(self.root):
            if job.state != RECORDING:
                continue
            if self.job is None and job.pids and all(_alive_meeting_pid(p) for p in job.pids):
                self.job = job
                self._logger.info("event=meeting_adopted dir=%s", job.dir.name)
                continue
            for pid in job.pids:
                if _alive_meeting_pid(pid):
                    _stop_pid(pid)
            job.advance(RECORDED, stopped=time.time(), pids=[])
            self._logger.info("event=meeting_recovered dir=%s", job.dir.name)
            closed.append(job)
        return closed


def _stop_pid(pid: int, timeout: float = 3.0) -> None:
    """SIGINT a meeting parec (flushes its buffer), escalate if needed."""
    if not _alive_meeting_pid(pid):
        return
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGKILL):
        try:
            os.kill(pid, sig)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                done, _ = os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                done = 0 if _alive_meeting_pid(pid) else pid
            if done == pid or not _alive_meeting_pid(pid):
                return
            time.sleep(0.05)
