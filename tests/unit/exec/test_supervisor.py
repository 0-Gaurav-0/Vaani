from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

from vaani.config import Settings
from vaani.exec import Command, Supervisor
from vaani.exec.proc import pid_alive
from vaani.exec.supervisor import (
    LOG_LIMIT_BYTES,
    Job,
    discover_port,
    read_jobs_registry,
    truncate_log_file,
    write_jobs_registry,
)


def _settings(tmp_path: Path) -> Settings:
    settings = Settings.from_home(tmp_path, platform=sys.platform)
    settings.prepare()
    return settings


def _script(tmp_path: Path, name: str, body: str) -> str:
    path = tmp_path / name
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)
    return str(path)


def test_discover_port_first_200_lines():
    lines = ["noise"] * 10 + ["Serving on http://127.0.0.1:5173"] + ["x"] * 5
    assert discover_port("\n".join(lines)) == 5173
    buried = ["nope"] * 200 + ["http://localhost:9999"]
    assert discover_port("\n".join(buried)) is None


def test_truncate_log_keeps_last_256kb(tmp_path: Path):
    path = tmp_path / "big.log"
    prefix = b"DROP-" * 1000
    keep = b"K" * (LOG_LIMIT_BYTES - 10) + b"END\n"
    path.write_bytes(prefix + keep)
    assert path.stat().st_size > LOG_LIMIT_BYTES
    truncate_log_file(path)
    data = path.read_bytes()
    assert len(data) <= LOG_LIMIT_BYTES
    assert data.endswith(b"END\n")
    assert b"DROP-" not in data[:20] or data.endswith(keep[-20:])


def test_registry_atomic_roundtrip(tmp_path: Path):
    path = tmp_path / "jobs.json"
    job = Job(
        key="dev",
        verb="project.dev.start",
        argv=("npm", "run", "dev"),
        cwd="/tmp/proj",
        pid=4242,
        started_at=1.5,
        log_path="/tmp/dev.log",
        port=3000,
        status="running",
    )
    write_jobs_registry(path, {"dev": job})
    assert path.is_file()
    assert not path.with_suffix(".json.tmp").exists()
    loaded = read_jobs_registry(path)
    assert loaded["dev"].argv == ("npm", "run", "dev")
    assert loaded["dev"].port == 3000


def test_lifecycle_start_status_logs_stop(tmp_path: Path):
    settings = _settings(tmp_path)
    script = _script(
        tmp_path,
        "serve",
        'echo "ready at http://localhost:8765"\nwhile true; do sleep 1; done\n',
    )
    sup = Supervisor(settings)
    job = sup.start("dev", Command(argv=(script,), cwd=tmp_path), verb="project.dev.start")
    try:
        assert job.key == "dev"
        assert job.verb == "project.dev.start"
        assert job.pid > 0
        assert pid_alive(job.pid)
        # Allow log flush + status refresh for port.
        deadline = time.monotonic() + 2.0
        port = None
        while time.monotonic() < deadline:
            st = sup.status("dev")
            assert st is not None
            port = st.port
            if port == 8765:
                break
            time.sleep(0.05)
        assert port == 8765
        text = sup.logs("dev", tail=50)
        assert "8765" in text
        assert settings.jobs_path.is_file()
        assert "dev" in json.loads(settings.jobs_path.read_text())
    finally:
        assert sup.stop("dev") is True
    assert not pid_alive(job.pid)
    assert sup.status("dev") is None


def test_restart_preserves_argv(tmp_path: Path):
    settings = _settings(tmp_path)
    script = _script(tmp_path, "loop", "while true; do sleep 1; done\n")
    sup = Supervisor(settings)
    first = sup.start("worker", Command(argv=(script, "keep"), cwd=tmp_path))
    try:
        second = sup.restart("worker")
        assert second.argv == first.argv == (script, "keep")
        assert second.pid != first.pid
        assert pid_alive(second.pid)
        assert not pid_alive(first.pid)
    finally:
        sup.stop("worker")


def test_registry_survives_simulated_vaani_restart(tmp_path: Path):
    settings = _settings(tmp_path)
    script = _script(tmp_path, "persist", "while true; do sleep 1; done\n")
    first = Supervisor(settings)
    job = first.start("persist", Command(argv=(script,), cwd=tmp_path))
    try:
        # Simulate Vaani process exit: drop in-memory handles, new supervisor boots.
        second = Supervisor(settings)
        second.adopt_or_clear()
        adopted = second.status("persist")
        assert adopted is not None
        assert adopted.pid == job.pid
        assert adopted.argv == job.argv
        assert pid_alive(adopted.pid)
        assert second.stop("persist") is True
    finally:
        if pid_alive(job.pid):
            Supervisor(settings).stop("persist", forceful=True)


def test_pid_reuse_rejected_without_argv_match(tmp_path: Path):
    settings = _settings(tmp_path)
    # Live pid belonging to this test process, but argv will not match.
    decoy = Job(
        key="spoof",
        verb="spoof",
        argv=("/nonexistent/decoy-bin", "--never"),
        cwd=os.fspath(tmp_path),
        pid=os.getpid(),
        started_at=time.time(),
        log_path=os.fspath(tmp_path / "spoof.log"),
        status="running",
    )
    write_jobs_registry(settings.jobs_path, {"spoof": decoy})
    assert pid_alive(os.getpid())
    sup = Supervisor(settings)
    sup.adopt_or_clear()
    assert sup.status("spoof") is None
    assert read_jobs_registry(settings.jobs_path) == {}


@pytest.mark.skipif(os.name == "nt", reason="POSIX group-kill orphan check")
def test_group_kill_leaves_no_orphan(tmp_path: Path):
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
        assert child_pid_file.exists()
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
