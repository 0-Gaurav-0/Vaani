"""Managed long-running jobs: detached spawn, registry, logs, adopt-or-clear."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from vaani.config import Settings, child_environment
from vaani.exec.proc import pid_alive, stop_with_escalation
from vaani.exec.runner import Command

LOG_LIMIT_BYTES = 256 * 1024
_PORT_RE = re.compile(r"https?://(?:localhost|127\.0\.0\.1):(\d+)", re.IGNORECASE)
_SAFE_KEY = re.compile(r"[^A-Za-z0-9_.-]+")


@dataclass(frozen=True)
class Job:
    key: str
    verb: str
    argv: tuple[str, ...]
    cwd: str
    pid: int
    started_at: float
    log_path: str
    port: int | None = None
    status: str = "running"


class Supervisor:
    """Process supervisor with an on-disk job registry at ``Settings.jobs_path``."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._jobs: dict[str, Job] = {}
        self._procs: dict[str, subprocess.Popen[bytes]] = {}
        settings.log_dir.mkdir(parents=True, exist_ok=True)
        (settings.log_dir / "jobs").mkdir(parents=True, exist_ok=True)
        settings.data_dir.mkdir(parents=True, exist_ok=True)

    def start(self, key: str, cmd: Command, *, verb: str | None = None) -> Job:
        if not key:
            raise ValueError("job key must be non-empty")
        if not cmd.argv:
            raise ValueError("command argv must be non-empty")
        existing = self._jobs.get(key)
        if existing is not None and existing.status == "running" and pid_alive(existing.pid):
            raise ValueError(f"job already running: {key}")

        cwd = Path(cmd.cwd) if cmd.cwd is not None else Path.cwd()
        cwd_s = os.fspath(cwd.resolve())
        log_path = self._log_path_for(key)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        # Fresh log per start so port discovery and tails stay relevant.
        log_path.write_bytes(b"")

        env = child_environment()
        if cmd.env_extra:
            env.update(dict(cmd.env_extra))

        popen_kwargs: dict[str, object] = {
            "args": list(cmd.argv),
            "cwd": cwd_s,
            "env": env,
            "stdin": subprocess.DEVNULL,
            "shell": False,
        }
        if os.name == "nt":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True

        with log_path.open("ab") as logf:
            popen_kwargs["stdout"] = logf
            popen_kwargs["stderr"] = subprocess.STDOUT
            proc = subprocess.Popen(**popen_kwargs)  # type: ignore[arg-type]

        job = Job(
            key=key,
            verb=verb if verb is not None else key,
            argv=tuple(cmd.argv),
            cwd=cwd_s,
            pid=int(proc.pid),
            started_at=time.time(),
            log_path=os.fspath(log_path),
            port=None,
            status="running",
        )
        self._procs[key] = proc
        self._jobs[key] = job
        self._persist()
        # Port may appear quickly; refresh once without blocking long.
        return self._refresh_job(key) or job

    def stop(self, key: str, *, forceful: bool = False) -> bool:
        job = self._jobs.get(key)
        if job is None:
            return False
        proc = self._procs.get(key)
        stop_with_escalation(proc, pid=job.pid, forceful=forceful)
        self._procs.pop(key, None)
        finished = Job(
            key=job.key,
            verb=job.verb,
            argv=job.argv,
            cwd=job.cwd,
            pid=job.pid,
            started_at=job.started_at,
            log_path=job.log_path,
            port=job.port,
            status="stopped",
        )
        self._jobs[key] = finished
        self._persist()
        # Drop stopped entries from the live registry so boot adoption stays lean.
        del self._jobs[key]
        self._persist()
        return True

    def restart(self, key: str) -> Job:
        job = self.status(key)
        if job is None:
            raise KeyError(key)
        argv = job.argv
        cwd = job.cwd
        verb = job.verb
        if job.status == "running" and pid_alive(job.pid):
            self.stop(key, forceful=False)
        elif key in self._jobs:
            self._procs.pop(key, None)
            del self._jobs[key]
            self._persist()
        return self.start(key, Command(argv=argv, cwd=Path(cwd)), verb=verb)

    def status(self, key: str) -> Job | None:
        return self._refresh_job(key)

    def list_jobs(self) -> tuple[Job, ...]:
        """Refresh and return all known jobs (running and recently exited)."""
        # Merge on-disk entries that may not be in memory yet (e.g. after restart).
        for key, job in self._load().items():
            if key not in self._jobs:
                self._jobs[key] = job
        keys = tuple(self._jobs)
        out: list[Job] = []
        for key in keys:
            job = self._refresh_job(key)
            if job is not None:
                out.append(job)
        return tuple(out)

    def logs(self, key: str, *, tail: int = 200) -> str:
        job = self._jobs.get(key)
        if job is None:
            loaded = self._load()
            job = loaded.get(key)
            if job is None:
                return ""
            self._jobs[key] = job
        path = Path(job.log_path)
        truncate_log_file(path)
        return _read_log_text(path, tail=tail)

    def adopt_or_clear(self) -> None:
        """On boot: keep jobs whose pid is alive and argv still matches; else clear."""
        loaded = self._load()
        kept: dict[str, Job] = {}
        for key, job in loaded.items():
            if not pid_alive(job.pid):
                continue
            if not argv_matches(job.pid, job.argv):
                continue
            kept[key] = Job(
                key=job.key,
                verb=job.verb,
                argv=job.argv,
                cwd=job.cwd,
                pid=job.pid,
                started_at=job.started_at,
                log_path=job.log_path,
                port=job.port,
                status="running",
            )
        self._jobs = kept
        # Adopted jobs have no live Popen handle.
        self._procs = {k: p for k, p in self._procs.items() if k in kept}
        self._persist()

    # --- internals ---------------------------------------------------------

    def _log_path_for(self, key: str) -> Path:
        safe = _SAFE_KEY.sub("_", key).strip("._") or "job"
        return self._settings.log_dir / "jobs" / f"{safe}.log"

    def _refresh_job(self, key: str) -> Job | None:
        job = self._jobs.get(key)
        if job is None:
            return None
        alive = pid_alive(job.pid)
        port = job.port
        if alive and port is None:
            port = discover_port(_read_log_text(Path(job.log_path), tail=200))
        status = "running" if alive else "exited"
        if port != job.port or status != job.status:
            job = Job(
                key=job.key,
                verb=job.verb,
                argv=job.argv,
                cwd=job.cwd,
                pid=job.pid,
                started_at=job.started_at,
                log_path=job.log_path,
                port=port,
                status=status,
            )
            self._jobs[key] = job
            self._persist()
        if not alive:
            self._procs.pop(key, None)
        return job

    def _persist(self) -> None:
        write_jobs_registry(self._settings.jobs_path, self._jobs)

    def _load(self) -> dict[str, Job]:
        return read_jobs_registry(self._settings.jobs_path)


def _read_log_text(path: Path, *, tail: int | None = None) -> str:
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if tail is None or tail < 0:
        return text
    return "\n".join(text.splitlines()[-tail:])


def truncate_log_file(path: Path | str, *, limit: int = LOG_LIMIT_BYTES) -> None:
    """Keep the last ``limit`` bytes of a log via truncate-and-rotate rewrite."""
    target = Path(path)
    try:
        size = target.stat().st_size
    except OSError:
        return
    if size <= limit:
        return
    try:
        with target.open("rb") as fh:
            fh.seek(size - limit)
            data = fh.read()
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, target)
    except OSError:
        return


def discover_port(log_text: str) -> int | None:
    """First localhost URL port in the first 200 lines of log text."""
    for index, line in enumerate(log_text.splitlines()):
        if index >= 200:
            break
        match = _PORT_RE.search(line)
        if match is not None:
            try:
                return int(match.group(1))
            except ValueError:
                continue
    return None


def write_jobs_registry(path: Path | str, jobs: dict[str, Job]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {key: _job_to_json(job) for key, job in jobs.items()}
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, target)
    try:
        target.chmod(0o600)
    except OSError:
        pass


def read_jobs_registry(path: Path | str) -> dict[str, Job]:
    target = Path(path)
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    jobs: dict[str, Job] = {}
    for key, value in raw.items():
        job = _job_from_json(str(key), value)
        if job is not None:
            jobs[job.key] = job
    return jobs


def argv_matches(pid: int, expected: tuple[str, ...]) -> bool:
    if not expected:
        return False
    joined = " ".join(expected)
    raw = read_process_cmdline_raw(pid)
    if raw is not None and raw == joined:
        return True
    # Soft: resolved executable path may differ; compare basenames + tail.
    if raw is not None:
        exp0 = Path(expected[0]).name
        if raw == f"{exp0} " + " ".join(expected[1:]) or raw.endswith(joined):
            return True
        # `/path/python -c …` vs registered interpreter path differences.
        parts = raw.split(" ", 1)
        if parts and Path(parts[0]).name == exp0:
            rest = parts[1] if len(parts) > 1 else ""
            if rest == " ".join(expected[1:]):
                return True

    actual = read_process_argv(pid)
    if actual is None:
        return False
    if actual == expected:
        return True
    if len(actual) >= len(expected) and actual[: len(expected)] == expected:
        return True
    if len(actual) == len(expected):
        if Path(actual[0]).name == Path(expected[0]).name and actual[1:] == expected[1:]:
            return True
    return " ".join(actual) == joined


def read_process_cmdline_raw(pid: int) -> str | None:
    """Return a display/compare cmdline string for adoption matching — never for shell."""
    if pid <= 0:
        return None
    if sys.platform.startswith("linux"):
        parts = read_process_argv(pid)
        return None if parts is None else " ".join(parts)
    if os.name == "nt":
        parts = read_process_argv(pid)
        return None if parts is None else subprocess.list2cmdline(list(parts))
    try:
        completed = subprocess.run(
            ["ps", "-p", str(pid), "-o", "args="],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    line = (completed.stdout or "").strip()
    if not line or completed.returncode != 0:
        return None
    return line


def read_process_argv(pid: int) -> tuple[str, ...] | None:
    if pid <= 0:
        return None
    if sys.platform.startswith("linux"):
        try:
            raw = Path(f"/proc/{pid}/cmdline").read_bytes()
        except OSError:
            return None
        if not raw:
            return None
        parts = [p.decode("utf-8", "surrogateescape") for p in raw.split(b"\0") if p]
        return tuple(parts) if parts else None

    if os.name == "nt":
        return _windows_process_argv(pid)

    # macOS / BSD: ``ps`` args column (best-effort; spaced args are ambiguous).
    line = read_process_cmdline_raw(pid)
    if line is None:
        return None
    return tuple(line.split())


def _windows_process_argv(pid: int) -> tuple[str, ...] | None:
    script = (
        f"$p = Get-CimInstance Win32_Process -Filter \"ProcessId = {int(pid)}\"; "
        "if ($null -eq $p) { exit 1 }; "
        "[Console]::Out.Write($p.CommandLine)"
    )
    try:
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                script,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    line = (completed.stdout or "").strip()
    if not line or completed.returncode != 0:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        size = wintypes.INT()
        ptr = ctypes.windll.shell32.CommandLineToArgvW(line, ctypes.byref(size))  # type: ignore[attr-defined]
        if not ptr:
            return tuple(line.split())
        try:
            args = tuple(ptr[i] for i in range(size.value))
        finally:
            ctypes.windll.kernel32.LocalFree(ptr)  # type: ignore[attr-defined]
        return args
    except Exception:
        return tuple(line.split())


def _job_to_json(job: Job) -> dict[str, object]:
    data = asdict(job)
    data["argv"] = list(job.argv)
    return data


def _job_from_json(key: str, value: object) -> Job | None:
    if not isinstance(value, dict):
        return None
    try:
        argv_raw = value.get("argv", ())
        if not isinstance(argv_raw, (list, tuple)):
            return None
        argv = tuple(str(part) for part in argv_raw)
        return Job(
            key=str(value.get("key", key)),
            verb=str(value.get("verb", key)),
            argv=argv,
            cwd=str(value.get("cwd", "")),
            pid=int(value["pid"]),
            started_at=float(value.get("started_at", 0.0)),
            log_path=str(value.get("log_path", "")),
            port=(int(value["port"]) if value.get("port") is not None else None),
            status=str(value.get("status", "running")),
        )
    except (KeyError, TypeError, ValueError):
        return None
