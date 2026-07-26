"""Cross-platform process-group stop helpers."""
from __future__ import annotations

import os
import signal
import subprocess


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


stop_process = _stop_process
