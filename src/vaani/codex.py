"""Bounded, cancellable Codex CLI runner and headless result adapter."""
from __future__ import annotations
import os, signal, subprocess, threading
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class CodexResult:
    stdout: str
    stderr: str
    returncode: int
    timed_out: bool = False
    cancelled: bool = False

def _redact(value: str) -> str:
    import re
    return re.sub(r'(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*\S+', r'\1=[REDACTED]', value)

def _stop_process(proc: subprocess.Popen[str] | subprocess.Popen[bytes] | None, *, forceful: bool = False) -> None:
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

class CodexRunner:
    def __init__(self, executable: str = "codex", cwd: str | None = None, timeout: float = 30.0):
        default_cwd = os.environ.get("VAANI_ASSISTANT_CWD") or str(Path.home())
        self.executable, self.cwd, self.timeout = executable, cwd or default_cwd, timeout
        self._proc = None
        self._cancel = threading.Event()
    @staticmethod
    def command_for(executable: str, prompt: str) -> list[str]:
        return [
            executable, "exec", "--ephemeral", "--ignore-user-config",
            "--color", "never", "-c", 'model_reasoning_effort="low"', prompt,
        ]
    def cancel(self):
        self._cancel.set()
        _stop_process(self._proc, forceful=False)
    def run(self, prompt: str, *, timeout: float | None = None) -> CodexResult:
        self._cancel.clear()
        env = {k:v for k,v in os.environ.items() if k not in {"OPENAI_API_KEY", "GROQ_API_KEY"}}
        try:
            # Ephemeral + ignore-user-config keeps unrelated MCP servers from
            # blocking a voice request on interactive authentication.
            self._proc = subprocess.Popen(self.command_for(self.executable, prompt), cwd=self.cwd, env=env,
                                          start_new_session=True,
                                          stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try: out, err = self._proc.communicate(timeout=timeout or self.timeout)
            except subprocess.TimeoutExpired:
                _stop_process(self._proc, forceful=True)
                out, err = self._proc.communicate()
                return CodexResult(_redact(out), _redact(err), -1, timed_out=True)
            return CodexResult(_redact(out), _redact(err), self._proc.returncode, cancelled=self._cancel.is_set())
        except Exception as exc:
            return CodexResult("", _redact(str(exc)), -1)
        finally: self._proc = None

class ResultWindow:
    def __init__(self, sink=None): self.sink = sink
    def show(self, result: CodexResult):
        text = result.stdout.strip() or result.stderr.strip() or "Codex returned no output."
        if result.timed_out: text = "Codex timed out.\n\n" + text
        if result.cancelled: text = "Codex request cancelled.\n\n" + text
        if self.sink: self.sink(text)

    def show_text(self, text: str):
        if self.sink: self.sink(text)
