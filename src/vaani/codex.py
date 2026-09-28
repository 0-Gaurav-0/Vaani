"""Bounded, cancellable assistant-agent CLI runner and headless result adapter.

Default agent is Hermes (`hermes -z`). Override with ``VAANI_AGENT_BIN``;
falls back to Codex CLI only when Hermes is not on ``PATH``.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


logger = logging.getLogger("vaani.codex")

# Always preload on Vaani→Hermes handoffs (see ~/.agents/skills/vaani/SKILL.md).
VAANI_HERMES_SKILL = "vaani"


def merge_hermes_skills(*skill_ids: str | None) -> str | None:
    """Comma-separated Hermes ``--skills`` list; always includes ``vaani`` first."""
    ordered: list[str] = []
    for sid in (VAANI_HERMES_SKILL, *skill_ids):
        name = (sid or "").strip()
        if not name or name in ordered:
            continue
        # Allow "basecamp" or already-merged "vaani,basecamp"
        for part in name.split(","):
            part = part.strip()
            if part and part not in ordered:
                ordered.append(part)
    return ",".join(ordered) if ordered else None


@dataclass(frozen=True)
class CodexResult:
    stdout: str
    stderr: str
    returncode: int
    timed_out: bool = False
    cancelled: bool = False
    session_id: str | None = None


@dataclass
class HandoffJob:
    """Background Hermes/Codex oneshot that releases the UI as soon as accepted."""

    prompt: str
    executable: str
    cwd: str
    timeout: float
    on_accepted: Callable[[str | None], None] | None = None
    on_complete: Callable[[CodexResult], None] | None = None
    session_id: str | None = None
    skill_id: str | None = None
    resume_session: str | None = None
    _proc: subprocess.Popen[str] | None = field(default=None, repr=False)
    _cancel: threading.Event = field(default_factory=threading.Event, repr=False)
    _accepted: threading.Event = field(default_factory=threading.Event, repr=False)
    _thread: threading.Thread | None = field(default=None, repr=False)
    _usage_path: Path | None = field(default=None, repr=False)
    _started_at: float = 0.0

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run, name="vaani-agent-handoff", daemon=True
        )
        self._thread.start()

    def cancel(self) -> None:
        self._cancel.set()
        _stop_process(self._proc, forceful=False)
        if self.session_id:
            try:
                from .hermes_notify import delete_hermes_session

                delete_hermes_session(self.session_id)
            except Exception:
                pass

    def _command(self) -> list[str]:
        hermes = not _is_codex_executable(self.executable)
        skills = merge_hermes_skills(self.skill_id) if hermes else self.skill_id
        if self.skill_id and not self.resume_session:
            cmd = CodexRunner.command_for_skill(
                self.executable, self.prompt, skill_id=skills
            )
        else:
            cmd = CodexRunner.command_for(
                self.executable, self.prompt, skills=skills if hermes else None
            )
        if self.resume_session and hermes:
            cmd.extend(["--resume", self.resume_session])
        if hermes and self._usage_path is not None:
            cmd.extend(["--usage-file", str(self._usage_path)])
        return cmd

    def _read_usage_session(self) -> str | None:
        path = self._usage_path
        if path is None or not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        sid = data.get("session_id")
        return str(sid) if sid else None

    def _find_session_in_db(self) -> str | None:
        db = Path.home() / ".hermes" / "state.db"
        if not db.is_file() or self._started_at <= 0:
            return None
        cwd = str(Path(self.cwd).resolve())
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=1.0)
            try:
                row = con.execute(
                    "SELECT id FROM sessions "
                    "WHERE source = 'cli' AND started_at >= ? "
                    "AND (cwd = ? OR cwd LIKE ?) "
                    "ORDER BY started_at DESC LIMIT 1",
                    (self._started_at - 2.0, cwd, f"%{Path(cwd).name}"),
                ).fetchone()
            finally:
                con.close()
        except Exception:
            return None
        return str(row[0]) if row else None

    def _discover_session(self) -> str | None:
        return self._read_usage_session() or self._find_session_in_db()

    def _run(self) -> None:
        usage_fd, usage_name = tempfile.mkstemp(prefix="vaani-hermes-usage-", suffix=".json")
        os.close(usage_fd)
        self._usage_path = Path(usage_name)
        try:
            self._usage_path.write_text("{}", encoding="utf-8")
        except Exception:
            pass
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in {"OPENAI_API_KEY", "GROQ_API_KEY"}
        }
        result = CodexResult("", "handoff failed to start", -1)
        try:
            Path(self.cwd).mkdir(parents=True, exist_ok=True)
            self._started_at = time.time()
            self._proc = subprocess.Popen(
                self._command(),
                cwd=self.cwd,
                env=env,
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            # UI release: subprocess accepted the prompt (Hermes session row
            # often appears much later after MCP/agent setup).
            if not self._cancel.is_set() and not self._accepted.is_set():
                self._accepted.set()
                if self.on_accepted:
                    try:
                        self.on_accepted(self.session_id)
                    except Exception:
                        logger.exception("handoff on_accepted failed")

            # Keep looking for a session id for notification deep-links.
            deadline = time.monotonic() + max(5.0, min(self.timeout, 120.0))
            while (
                not self._cancel.is_set()
                and self._proc.poll() is None
                and time.monotonic() < deadline
            ):
                sid = self._discover_session()
                if sid:
                    self.session_id = sid
                    break
                time.sleep(0.25)

            try:
                out, err = self._proc.communicate(timeout=self.timeout)
            except subprocess.TimeoutExpired:
                _stop_process(self._proc, forceful=True)
                out, err = self._proc.communicate()
                result = CodexResult(
                    _redact(out or ""),
                    _redact(err or ""),
                    -1,
                    timed_out=True,
                    session_id=self.session_id or self._discover_session(),
                )
            else:
                if not self.session_id:
                    self.session_id = self._discover_session()
                result = CodexResult(
                    _redact(out or ""),
                    _redact(err or ""),
                    int(self._proc.returncode or 0),
                    cancelled=self._cancel.is_set(),
                    session_id=self.session_id,
                )
        except Exception as exc:
            result = CodexResult("", _redact(str(exc)), -1, session_id=self.session_id)
        finally:
            self._proc = None
            if self._usage_path is not None:
                try:
                    self._usage_path.unlink(missing_ok=True)
                except Exception:
                    pass
            if self.on_complete:
                try:
                    self.on_complete(result)
                except Exception:
                    logger.exception("handoff on_complete failed")


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


def _default_executable() -> str:
    """Resolve agent CLI: ``VAANI_AGENT_BIN``, else hermes, else codex."""
    env = (os.environ.get("VAANI_AGENT_BIN") or "").strip()
    if env:
        return env
    if shutil.which("hermes"):
        return "hermes"
    return "codex"


def _is_codex_executable(executable: str) -> bool:
    name = Path(executable).name.lower()
    return name == "codex" or name.startswith("codex.")


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
    """Run Hermes (default) or Codex oneshot prompts with cancel + timeout."""

    def __init__(
        self,
        executable: str | None = None,
        cwd: str | None = None,
        timeout: float = 30.0,
    ):
        # Hermes desktop groups sessions by project folder; keep handoffs in
        # ~/vaani-agent/vani-task (Hermes project "Vaani agent"), not Home/Vaani-main.
        default_cwd = os.environ.get("VAANI_ASSISTANT_CWD") or str(
            Path.home() / "vaani-agent" / "vani-task"
        )
        self.executable = executable if executable is not None else _default_executable()
        self.cwd = cwd or default_cwd
        self.timeout = timeout
        self.skill_timeout = float(os.environ.get("VAANI_SKILL_TIMEOUT", "120") or 120)
        self._proc = None
        self._cancel = threading.Event()

    @staticmethod
    def command_for(
        executable: str,
        prompt: str,
        *,
        skills: str | None = None,
    ) -> list[str]:
        if _is_codex_executable(executable):
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
        # Hermes oneshot: final response only on stdout; --yolo for tool approvals.
        cmd = [
            executable,
            "-z",
            prompt,
            "--yolo",
            "--reasoning",
            "low",
        ]
        if skills:
            cmd.extend(["--skills", skills])
        return cmd

    @staticmethod
    def command_for_skill(
        executable: str,
        prompt: str,
        *,
        mcp_names: list[str] | None = None,
        skill_id: str | None = None,
    ) -> list[str]:
        """Build argv for a skill run.

        Codex: empty MCP set via temp ``CODEX_HOME`` (see ``prepare_skill_codex_home``).
        Hermes: skill body is in ``prompt``; optional ``--skills`` when ``skill_id``
        maps. MCP allowlist via temp CODEX_HOME does not apply to Hermes.
        """
        _ = mcp_names or []
        if _is_codex_executable(executable):
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
        cmd = [
            executable,
            "-z",
            prompt,
            "--yolo",
            "--reasoning",
            "low",
        ]
        if skill_id:
            cmd.extend(["--skills", skill_id])
        return cmd

    def cancel(self):
        self._cancel.set()
        _stop_process(self._proc, forceful=False)

    def start_handoff(
        self,
        prompt: str,
        *,
        on_accepted: Callable[[str | None], None] | None = None,
        on_complete: Callable[[CodexResult], None] | None = None,
        timeout: float | None = None,
        skill_id: str | None = None,
        resume_session: str | None = None,
    ) -> HandoffJob:
        """Start a background agent job; ``on_accepted`` fires once Popen succeeds."""
        handoff_timeout = timeout
        if handoff_timeout is None:
            handoff_timeout = float(
                os.environ.get("VAANI_HANDOFF_TIMEOUT", "600") or 600
            )
        job = HandoffJob(
            prompt=prompt,
            executable=self.executable,
            cwd=self.cwd,
            timeout=handoff_timeout,
            on_accepted=on_accepted,
            on_complete=on_complete,
            skill_id=skill_id,
            resume_session=resume_session,
        )
        # Resuming: seed known id so notifications deep-link immediately.
        if resume_session:
            job.session_id = resume_session
        job.start()
        return job

    def run(self, prompt: str, *, timeout: float | None = None) -> CodexResult:
        self._cancel.clear()
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in {"OPENAI_API_KEY", "GROQ_API_KEY"}
        }
        try:
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
        skill_id: str | None = None,
    ) -> CodexResult:
        self._cancel.clear()
        mcp_names = [m for m in mcps if m]
        prompt = build_skill_prompt(skill_body, utterance)
        tmp_home: Path | None = None
        try:
            env = {
                k: v
                for k, v in os.environ.items()
                if k not in {"OPENAI_API_KEY", "GROQ_API_KEY"}
            }
            if _is_codex_executable(self.executable):
                tmp_home = prepare_skill_codex_home(mcp_names, source_home=source_home)
                env["CODEX_HOME"] = str(tmp_home)
            cmd = self.command_for_skill(
                self.executable,
                prompt,
                mcp_names=mcp_names,
                skill_id=skill_id,
            )
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
        text = result.stdout.strip() or result.stderr.strip() or "Assistant returned no output."
        if result.timed_out:
            text = "Assistant timed out.\n\n" + text
        if result.cancelled:
            text = "Assistant request cancelled.\n\n" + text
        if self.sink:
            self.sink(text)

    def show_text(self, text: str):
        if self.sink:
            self.sink(text)
