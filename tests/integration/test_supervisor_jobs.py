"""Integration: real child processes for supervisor lifecycle and group-kill."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from vaani.config import Settings
from vaani.exec import Command, Supervisor
from vaani.exec.proc import pid_alive


def _settings(tmp_path: Path) -> Settings:
    settings = Settings.from_home(tmp_path, platform=sys.platform)
    settings.prepare()
    return settings


def _script(tmp_path: Path, name: str, body: str) -> str:
    path = tmp_path / name
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)
    return str(path)


def test_supervisor_lifecycle_integration(tmp_path: Path):
    settings = _settings(tmp_path)
    script = _script(
        tmp_path,
        "svc",
        'printf "listen http://127.0.0.1:4321\\n"\nwhile true; do sleep 1; done\n',
    )
    sup = Supervisor(settings)
    job = sup.start("svc", Command(argv=(script,), cwd=tmp_path))
    try:
        assert pid_alive(job.pid)
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            st = sup.status("svc")
            if st is not None and st.port == 4321:
                break
            time.sleep(0.05)
        assert sup.status("svc") is not None
        assert "4321" in (sup.logs("svc") or "")
        restarted = sup.restart("svc")
        assert restarted.argv == job.argv
        assert restarted.pid != job.pid
    finally:
        sup.stop("svc", forceful=True)


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group orphan check")
def test_supervisor_group_kill_no_orphan_integration(tmp_path: Path):
    settings = _settings(tmp_path)
    child_pid_file = tmp_path / "child.pid"
    script = _script(
        tmp_path,
        "nest",
        f"""
sleep 60 &
echo $! > "{child_pid_file}"
wait
""",
    )
    sup = Supervisor(settings)
    job = sup.start("nest", Command(argv=(script,), cwd=tmp_path))
    try:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not child_pid_file.exists():
            time.sleep(0.05)
        child_pid = int(child_pid_file.read_text().strip())
        assert pid_alive(child_pid)
        assert sup.stop("nest") is True
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and (pid_alive(job.pid) or pid_alive(child_pid)):
            time.sleep(0.05)
        assert not pid_alive(job.pid)
        assert not pid_alive(child_pid)
    finally:
        if pid_alive(job.pid):
            sup.stop("nest", forceful=True)
