"""Portable stop/cancel channel for the recording indicator process.

Linux historically used SIGUSR1/2. Windows has no equivalent, so the daemon
and indicator share a small JSON control file under the cache directory.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Literal

Command = Literal["stop", "cancel", "option_0", "option_1", "option_2", "option_3", "option_4"]
Phase = Literal["recording", "processing", "answer"]
ALLOWED: frozenset[str] = frozenset(
    {"stop", "cancel", "option_0", "option_1", "option_2", "option_3", "option_4"}
)
ALLOWED_PHASES: frozenset[str] = frozenset({"recording", "processing", "answer"})


def control_path(cache_dir: Path | str) -> Path:
    return Path(cache_dir) / "indicator_control.json"


def phase_path(cache_dir: Path | str) -> Path:
    return Path(cache_dir) / "indicator_phase"


def answer_path(cache_dir: Path | str) -> Path:
    return Path(cache_dir) / "indicator_answer.json"


def resolve_control_path(
    *,
    explicit: str | os.PathLike[str] | None = None,
    cache_dir: str | os.PathLike[str] | None = None,
) -> Path:
    if explicit is not None:
        return Path(explicit)
    env = os.environ.get("VAANI_INDICATOR_CONTROL")
    if env:
        return Path(env)
    if cache_dir is not None:
        return control_path(cache_dir)
    amp = os.environ.get("VAANI_AMPLITUDE_PATH")
    if amp:
        return Path(amp).parent / "indicator_control.json"
    return Path("/tmp/vaani-indicator-control.json")


def write_command(path: Path | str, command: str) -> None:
    if command not in ALLOWED:
        raise ValueError(f"unknown indicator command: {command!r}")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"command": command, "ts_ms": int(time.time() * 1000)}
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, target)
    try:
        target.chmod(0o600)
    except OSError:
        pass


def read_command(path: Path | str) -> str | None:
    target = Path(path)
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    command = data.get("command")
    if command in ALLOWED:
        return str(command)
    return None


def clear_command(path: Path | str) -> None:
    target = Path(path)
    try:
        target.unlink()
    except FileNotFoundError:
        return
    except OSError:
        try:
            target.write_text("{}", encoding="utf-8")
        except OSError:
            pass


def write_state(path: Path | str, *, pid: int, state: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"pid": int(pid), "state": str(state), "updated_ms": int(time.time() * 1000)}
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, target)


def resolve_phase_path(
    *,
    explicit: str | os.PathLike[str] | None = None,
    cache_dir: str | os.PathLike[str] | None = None,
) -> Path:
    if explicit is not None:
        return Path(explicit)
    env = os.environ.get("VAANI_INDICATOR_PHASE")
    if env:
        return Path(env)
    control = resolve_control_path(cache_dir=cache_dir)
    return control.parent / "indicator_phase"


def write_phase(path: Path | str, phase: str) -> None:
    if phase not in ALLOWED_PHASES:
        raise ValueError(f"unknown indicator phase: {phase!r}")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(phase, encoding="utf-8")
    os.replace(tmp, target)
    try:
        target.chmod(0o600)
    except OSError:
        pass


def read_phase(path: Path | str) -> str:
    try:
        value = Path(path).read_text(encoding="utf-8").strip()
    except (FileNotFoundError, OSError):
        return "recording"
    return value if value in ALLOWED_PHASES else "recording"


def clear_phase(path: Path | str) -> None:
    try:
        Path(path).unlink()
    except FileNotFoundError:
        return
    except OSError:
        pass


def resolve_answer_path(
    *,
    explicit: str | os.PathLike[str] | None = None,
    cache_dir: str | os.PathLike[str] | None = None,
) -> Path:
    if explicit is not None:
        return Path(explicit)
    env = os.environ.get("VAANI_INDICATOR_ANSWER")
    if env:
        return Path(env)
    control = resolve_control_path(cache_dir=cache_dir)
    return control.parent / "indicator_answer.json"


def write_answer(
    path: Path | str,
    question: str,
    answer: str,
    *,
    options: list[str] | tuple[str, ...] | None = None,
) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload: dict = {"question": str(question), "answer": str(answer)}
    if options:
        payload["options"] = [str(item) for item in list(options)[:5]]
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, target)
    try:
        target.chmod(0o600)
    except OSError:
        pass


def read_answer(path: Path | str) -> dict:
    target = Path(path)
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError, TypeError):
        return {"question": "", "answer": "", "options": []}
    if not isinstance(data, dict):
        return {"question": "", "answer": "", "options": []}
    raw_opts = data.get("options")
    options: list[str] = []
    if isinstance(raw_opts, list):
        options = [str(item) for item in raw_opts[:5] if str(item).strip()]
    return {
        "question": str(data.get("question") or ""),
        "answer": str(data.get("answer") or ""),
        "options": options,
    }


def clear_answer(path: Path | str) -> None:
    target = Path(path)
    try:
        target.unlink()
    except FileNotFoundError:
        return
    except OSError:
        try:
            target.write_text("{}", encoding="utf-8")
        except OSError:
            pass
