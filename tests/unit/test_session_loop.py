from __future__ import annotations

import threading
import time

from vaani.session_loop import SessionLoop


def test_start_stop_idempotent():
    loop = SessionLoop(tick=0.01)
    loop.start()
    loop.start()
    assert loop._thread is not None and loop._thread.is_alive()
    loop.stop()
    loop.stop()
    assert loop._thread is None


def test_raising_job_does_not_kill_siblings():
    hits = []
    lock = threading.Lock()

    def boom():
        raise RuntimeError("boom")

    def ok():
        with lock:
            hits.append(time.monotonic())

    loop = SessionLoop(tick=0.01)
    loop.add("boom", boom, every=0.01)
    loop.add("ok", ok, every=0.01)
    loop.start()
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        with lock:
            if len(hits) >= 3:
                break
        time.sleep(0.01)
    loop.stop()
    with lock:
        assert len(hits) >= 3


def test_job_every_interval_respected():
    hits = []

    def tick():
        hits.append(time.monotonic())

    loop = SessionLoop(tick=0.01)
    loop.add("slow", tick, every=0.08)
    loop.start()
    time.sleep(0.25)
    loop.stop()
    assert 2 <= len(hits) <= 5
