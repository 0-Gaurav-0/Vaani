"""Cross-platform process-group stop helpers."""
from __future__ import annotations

import os
import signal
import subprocess
import time


def _stop_process(
    proc: subprocess.Popen[str] | subprocess.Popen[bytes] | None,
    *,
    forceful: bool = False,
) -> None:
    """Terminate a session-leader child without assuming POSIX killpg."""
    if proc is None or proc.poll() is not None:
        return
    if os.name == "nt" or not hasattr(os, "killpg"):
        try:
            (proc.kill if forceful else proc.terminate)()
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        return
    sig = signal.SIGKILL if forceful else signal.SIGTERM
    try:
        os.killpg(proc.pid, sig)
    except Exception:
        try:
            (proc.kill if forceful else proc.terminate)()
        except Exception:
            pass


def stop_pid(pid: int, *, forceful: bool = False) -> None:
    """Terminate a process-group leader by pid (adopted jobs after restart)."""
    if pid <= 0:
        return
    if os.name == "nt" or not hasattr(os, "killpg"):
        try:
            if forceful:
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    check=False,
                    capture_output=True,
                )
            else:
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T"],
                    check=False,
                    capture_output=True,
                )
        except Exception:
            try:
                os.kill(pid, signal.SIGTERM)
            except Exception:
                pass
        return
    sig = signal.SIGKILL if forceful else signal.SIGTERM
    try:
        os.killpg(pid, sig)
    except ProcessLookupError:
        return
    except Exception:
        try:
            os.kill(pid, sig)
        except Exception:
            pass


def stop_with_escalation(
    proc: subprocess.Popen[str] | subprocess.Popen[bytes] | None = None,
    *,
    pid: int | None = None,
    forceful: bool = False,
    grace_s: float = 2.0,
) -> None:
    """SIGTERM (or terminate), wait ``grace_s``, then SIGKILL if still alive."""
    if forceful:
        if proc is not None:
            _stop_process(proc, forceful=True)
        elif pid is not None:
            stop_pid(pid, forceful=True)
        return

    if proc is not None:
        _stop_process(proc, forceful=False)
        try:
            proc.wait(timeout=grace_s)
            return
        except subprocess.TimeoutExpired:
            _stop_process(proc, forceful=True)
            try:
                proc.wait(timeout=grace_s)
            except subprocess.TimeoutExpired:
                pass
        return

    if pid is None:
        return
    stop_pid(pid, forceful=False)
    deadline = time.monotonic() + grace_s
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return
        time.sleep(0.05)
    stop_pid(pid, forceful=True)


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


stop_process = _stop_process
