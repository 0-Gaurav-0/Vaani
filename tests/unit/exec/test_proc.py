from __future__ import annotations

import subprocess
import sys
import time

from vaani.exec.proc import _stop_process, stop_process


def test_stop_process_alias():
    assert stop_process is _stop_process


def test_stop_process_terminates_session(tmp_path):
    script = tmp_path / "slow"
    script.write_text("#!/bin/sh\nsleep 30\n")
    script.chmod(0o755)
    proc = subprocess.Popen(
        [str(script)],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        time.sleep(0.1)
        assert proc.poll() is None
        _stop_process(proc, forceful=True)
        proc.wait(timeout=2)
        assert proc.poll() is not None
    finally:
        if proc.poll() is None:
            proc.kill()


def test_stop_process_noop_when_finished():
    proc = subprocess.Popen(
        [sys.executable, "-c", "pass"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    proc.wait(timeout=5)
    _stop_process(proc, forceful=True)  # must not raise
