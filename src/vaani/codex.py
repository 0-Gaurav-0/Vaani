"""Bounded, cancellable Codex CLI runner and headless result adapter."""
from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
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
    return re.sub(
        r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*\S+",
        r"\1=[REDACTED]",
        value,
    )


def _stop_process(
    proc: subprocess.Popen[str] | subprocess.Popen[bytes] | None, *, forceful: bool = False
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


def _codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))


def extract_mcp_server_blocks(config_text: str, names: list[str]) -> str:
    """Return TOML fragments for the named ``[mcp_servers.*]`` sections only."""
    wanted = set(names)
    if not wanted:
        return ""
    lines = config_text.splitlines(keepends=True)
    blocks: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r"^\[(mcp_servers\.([^\].]+(?:\.[^\]]+)*))\]\s*$", line.strip())
        if not m:
            i += 1
            continue
        full = m.group(1)
        root = m.group(2).split(".", 1)[0]
        if root not in wanted:
            i += 1
            continue
        start = i
        i += 1
        while i < len(lines):
            nxt = lines[i].strip()
            if nxt.startswith("[") and nxt.endswith("]"):
                nxt_m = re.match(r"^\[mcp_servers\.([^\].]+)", nxt)
                if nxt_m and nxt_m.group(1).split(".", 1)[0] == root:
                    i += 1
                    continue
                if nxt.startswith("["):
                    break
            i += 1
        blocks.append("".join(lines[start:i]).rstrip() + "\n")
    return "\n".join(blocks).rstrip() + ("\n" if blocks else "")


def build_skill_prompt(skill_body: str, utterance: str) -> str:
    return (
        "You are executing a single prepared Agent Skill for a voice assistant.\n"
        "Follow the skill instructions below. Use only tools/MCPs available in this "
        "session (there may be none — prefer curl/shell when that is enough).\n\n"
        f"## Skill\n\n{skill_body.strip()}\n\n"
        f"## User request\n\n{utterance.strip()}\n"
    )


def prepare_skill_codex_home(
    mcp_names: list[str],
    *,
    source_home: Path | None = None,
) -> Path:
    """Temp CODEX_HOME with auth + only allowlisted MCP server blocks."""
    source = source_home or _codex_home()
    tmp = Path(tempfile.mkdtemp(prefix="vaani-codex-"))
    for name in ("auth.json", "auth"):
        src = source / name
        if src.exists():
            dest = tmp / name
            try:
                dest.symlink_to(src)
            except OSError:
                if src.is_file():
                    shutil.copy2(src, dest)
    config_src = source / "config.toml"
    mcp_toml = ""
    if mcp_names and config_src.is_file():
        mcp_toml = extract_mcp_server_blocks(
            config_src.read_text(encoding="utf-8"), list(mcp_names)
        )
    (tmp / "config.toml").write_text(
        'model_reasoning_effort = "low"\n\n' + mcp_toml,
        encoding="utf-8",
    )
    return tmp


class CodexRunner:
    def __init__(
        self, executable: str = "codex", cwd: str | None = None, timeout: float = 30.0
    ):
        default_cwd = os.environ.get("VAANI_ASSISTANT_CWD") or str(Path.home())
        self.executable, self.cwd, self.timeout = executable, cwd or default_cwd, timeout
        self.skill_timeout = float(os.environ.get("VAANI_SKILL_TIMEOUT", "120") or 120)
        self._proc = None
        self._cancel = threading.Event()

    @staticmethod
    def command_for(executable: str, prompt: str) -> list[str]:
        return [
            executable,
            "exec",
            "--ephemeral",
            "--ignore-user-config",
            "--color",
            "never",
            "-c",
            'model_reasoning_effort="low"',
            prompt,
        ]

    @staticmethod
    def command_for_skill(executable: str, prompt: str, *, mcp_names: list[str] | None = None) -> list[str]:
        """Build argv for a skill run.

        Chat fallback uses ``--ignore-user-config``. Skill runs also start from an
        empty MCP set; allowlisted servers are provided via a temp ``CODEX_HOME``
        (see ``prepare_skill_codex_home``), not via loading the full user config.
        ``mcp_names`` is recorded in argv as a comment-free marker for tests by
        including each name only inside the prompt/env contract — the command
        itself stays MCP-agnostic and never embeds unrelated server names.
        """
        _ = mcp_names or []
        return [
            executable,
            "exec",
            "--ephemeral",
            "--skip-git-repo-check",
            "--color",
            "never",
            "-c",
            'model_reasoning_effort="low"',
            prompt,
        ]

    def cancel(self):
        self._cancel.set()
        _stop_process(self._proc, forceful=False)

    def run(self, prompt: str, *, timeout: float | None = None) -> CodexResult:
        self._cancel.clear()
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in {"OPENAI_API_KEY", "GROQ_API_KEY"}
        }
        try:
            # Ephemeral + ignore-user-config keeps unrelated MCP servers from
            # blocking a voice request on interactive authentication.
            self._proc = subprocess.Popen(
                self.command_for(self.executable, prompt),
                cwd=self.cwd,
                env=env,
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                out, err = self._proc.communicate(timeout=timeout or self.timeout)
            except subprocess.TimeoutExpired:
                _stop_process(self._proc, forceful=True)
                out, err = self._proc.communicate()
                return CodexResult(_redact(out), _redact(err), -1, timed_out=True)
            return CodexResult(
                _redact(out),
                _redact(err),
                self._proc.returncode,
                cancelled=self._cancel.is_set(),
            )
        except Exception as exc:
            return CodexResult("", _redact(str(exc)), -1)
        finally:
            self._proc = None

    def run_skill(
        self,
        skill_body: str,
        utterance: str,
        *,
        mcps: list[str] | tuple[str, ...] = (),
        timeout: float | None = None,
        source_home: Path | None = None,
    ) -> CodexResult:
        self._cancel.clear()
        mcp_names = [m for m in mcps if m]
        prompt = build_skill_prompt(skill_body, utterance)
        tmp_home: Path | None = None
        try:
            tmp_home = prepare_skill_codex_home(mcp_names, source_home=source_home)
            env = {
                k: v
                for k, v in os.environ.items()
                if k not in {"OPENAI_API_KEY", "GROQ_API_KEY"}
            }
            env["CODEX_HOME"] = str(tmp_home)
            cmd = self.command_for_skill(self.executable, prompt, mcp_names=mcp_names)
            self._proc = subprocess.Popen(
                cmd,
                cwd=self.cwd,
                env=env,
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                out, err = self._proc.communicate(
                    timeout=timeout if timeout is not None else self.skill_timeout
                )
            except subprocess.TimeoutExpired:
                _stop_process(self._proc, forceful=True)
                out, err = self._proc.communicate()
                return CodexResult(_redact(out), _redact(err), -1, timed_out=True)
            return CodexResult(
                _redact(out),
                _redact(err),
                self._proc.returncode,
                cancelled=self._cancel.is_set(),
            )
        except Exception as exc:
            return CodexResult("", _redact(str(exc)), -1)
        finally:
            self._proc = None
            if tmp_home is not None:
                shutil.rmtree(tmp_home, ignore_errors=True)


class ResultWindow:
    def __init__(self, sink=None):
        self.sink = sink

    def show(self, result: CodexResult):
        text = result.stdout.strip() or result.stderr.strip() or "Codex returned no output."
        if result.timed_out:
            text = "Codex timed out.\n\n" + text
        if result.cancelled:
            text = "Codex request cancelled.\n\n" + text
        if self.sink:
            self.sink(text)

    def show_text(self, text: str):
        if self.sink:
            self.sink(text)
