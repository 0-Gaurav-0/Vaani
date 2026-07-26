"""Process-lifetime polling loop for controller side jobs.

One daemon thread ticks at ~50ms and runs registered callables. Used for
amplitude sampling, indicator control, and (later) pending-action TTL.
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class _Job:
    name: str
    fn: Callable[[], None]
    every: float
    next_at: float = 0.0


class SessionLoop:
    """One 50ms daemon thread for the process lifetime. Jobs are callables."""

    def __init__(
        self,
        *,
        tick: float = 0.05,
        logger: logging.Logger | None = None,
    ) -> None:
        self._tick = tick
        self._logger = logger or logging.getLogger("vaani")
        self._jobs: dict[str, _Job] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def add(self, name: str, fn: Callable[[], None], *, every: float = 0.05) -> None:
        with self._lock:
            self._jobs[name] = _Job(name=name, fn=fn, every=max(0.0, every), next_at=0.0)

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            thread = threading.Thread(
                target=self._run,
                name="vaani-session-loop",
                daemon=True,
            )
            self._thread = thread
            thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            thread = self._thread
            self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)

    def _run(self) -> None:
        while not self._stop.wait(self._tick):
            now = time.monotonic()
            with self._lock:
                jobs = list(self._jobs.values())
            for job in jobs:
                if now < job.next_at:
                    continue
                job.next_at = now + job.every
                try:
                    job.fn()
                except Exception:
                    self._logger.exception("session_loop job failed name=%s", job.name)
