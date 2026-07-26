"""Shared argv runner for verbs — argv lists only; never a shell command string."""
from __future__ import annotations

import os
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from vaani.config import child_environment
from vaani.exec.proc import _stop_process
from vaani.observability import _redact


@dataclass(frozen=True)
class Command:
    argv: tuple[str, ...]
    cwd: Path | None = None
    timeout: float = 20.0
    env_extra: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Completed:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    cancelled: bool = False


def powershell(script_args: Sequence[str]) -> tuple[str, ...]:
    """Build a PowerShell argv.

    This is the sole site that materializes a Windows ``-Command`` string.
    Each slot is single-quoted with PowerShell escaping (``'`` → ``''``).
    """
    command = " ".join(_ps_quote(arg) for arg in script_args)
    return ("powershell", "-NoProfile", "-NonInteractive", "-Command", command)


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def run(cmd: Command, *, cancel: threading.Event | None = None) -> Completed:
    """Run ``cmd.argv`` with an allowlisted env; always ``shell=False``."""
    env = child_environment()
    if cmd.env_extra:
        env.update(dict(cmd.env_extra))

    popen_kwargs: dict[str, object] = {
        "args": list(cmd.argv),
        "cwd": None if cmd.cwd is None else os.fspath(cmd.cwd),
        "env": env,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "shell": False,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True

    proc: subprocess.Popen[str] | None = None
    try:
        proc = subprocess.Popen(**popen_kwargs)  # type: ignore[arg-type]
        timed_out = False
        cancelled = False
        out = ""
        err = ""
        deadline = time.monotonic() + cmd.timeout
        slice_s = 0.05
        while True:
            if cancel is not None and cancel.is_set():
                cancelled = True
                _stop_process(proc, forceful=False)
                out, err = proc.communicate()
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                _stop_process(proc, forceful=True)
                out, err = proc.communicate()
                break
            try:
                out, err = proc.communicate(timeout=min(slice_s, remaining))
                cancelled = cancel is not None and cancel.is_set()
                break
            except subprocess.TimeoutExpired:
                continue
        returncode = -1 if timed_out else (proc.returncode if proc.returncode is not None else -1)
        return Completed(
            argv=cmd.argv,
            returncode=returncode,
            stdout=_redact(out or ""),
            stderr=_redact(err or ""),
            timed_out=timed_out,
            cancelled=cancelled,
        )
    except Exception as exc:
        return Completed(
            argv=cmd.argv,
            returncode=-1,
            stdout="",
            stderr=_redact(str(exc)),
        )
    finally:
        proc = None
