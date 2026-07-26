"""Portable stop/cancel/confirm channel for the recording indicator process.

Linux historically used SIGUSR1/2. Windows has no equivalent, so the daemon
and indicator share a small JSON control file under the cache directory.

Confirm commands are ``approve:<id>`` / ``reject:<id>`` (plan T2.1).
Disambiguation adds ``select:<id>:<n>`` (1-based option index, plan T4.5).
Static commands remain ``stop`` / ``cancel``.
"""
from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

Command = Literal["stop", "cancel"]
Phase = Literal["recording", "processing", "confirming", "working"]
ALLOWED: frozenset[str] = frozenset({"stop", "cancel"})
ALLOWED_PHASES: frozenset[str] = frozenset(
    {"recording", "processing", "confirming", "working"}
)

_CONFIRM_COMMAND = re.compile(r"^(approve|reject):([A-Za-z0-9_-]{1,64})$")
_SELECT_COMMAND = re.compile(r"^select:([A-Za-z0-9_-]{1,64}):([1-3])$")


def is_allowed_command(command: str) -> bool:
    """True for static stop/cancel, confirm, or select:<id>:<n>."""
    if command in ALLOWED:
        return True
    if _CONFIRM_COMMAND.match(command) is not None:
        return True
    return _SELECT_COMMAND.match(command) is not None


def parse_confirm_command(command: str) -> tuple[str, str] | None:
    """Return ``(approve|reject, id)`` or None when not a confirm command."""
    match = _CONFIRM_COMMAND.match(command)
    if match is None:
        return None
    return match.group(1), match.group(2)


def parse_select_command(command: str) -> tuple[str, int] | None:
    """Return ``(id, 0-based index)`` for ``select:<id>:<n>``, else None."""
    match = _SELECT_COMMAND.match(command)
    if match is None:
        return None
    return match.group(1), int(match.group(2)) - 1


def control_path(cache_dir: Path | str) -> Path:
    return Path(cache_dir) / "indicator_control.json"


def phase_path(cache_dir: Path | str) -> Path:
    return Path(cache_dir) / "indicator_phase"


def pending_path(cache_dir: Path | str) -> Path:
    return Path(cache_dir) / "indicator_pending"


def options_path(cache_dir: Path | str) -> Path:
    return Path(cache_dir) / "indicator_options.json"


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


def resolve_pending_path(
    *,
    explicit: str | os.PathLike[str] | None = None,
    cache_dir: str | os.PathLike[str] | None = None,
) -> Path:
    if explicit is not None:
        return Path(explicit)
    env = os.environ.get("VAANI_INDICATOR_PENDING")
    if env:
        return Path(env)
    control = resolve_control_path(cache_dir=cache_dir)
    return control.parent / "indicator_pending"


def write_command(path: Path | str, command: str) -> None:
    if not is_allowed_command(command):
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
    if isinstance(command, str) and is_allowed_command(command):
        return command
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


def write_pending_id(path: Path | str, action_id: str) -> None:
    if not action_id or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", action_id):
        raise ValueError(f"invalid pending action id: {action_id!r}")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(action_id, encoding="utf-8")
    os.replace(tmp, target)
    try:
        target.chmod(0o600)
    except OSError:
        pass


def read_pending_id(path: Path | str) -> str | None:
    try:
        value = Path(path).read_text(encoding="utf-8").strip()
    except (FileNotFoundError, OSError):
        return None
    if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
        return value
    return None


def clear_pending_id(path: Path | str) -> None:
    try:
        Path(path).unlink()
    except FileNotFoundError:
        return
    except OSError:
        pass


def resolve_options_path(
    *,
    explicit: str | os.PathLike[str] | None = None,
    cache_dir: str | os.PathLike[str] | None = None,
) -> Path:
    if explicit is not None:
        return Path(explicit)
    env = os.environ.get("VAANI_INDICATOR_OPTIONS")
    if env:
        return Path(env)
    control = resolve_control_path(cache_dir=cache_dir)
    return control.parent / "indicator_options.json"


def write_options(path: Path | str, labels: Sequence[str]) -> None:
    """Write pill option labels (at most 3) for disambiguation UI."""
    if isinstance(labels, (str, bytes)):
        raise TypeError("labels must be a sequence of strings")
    trimmed = [str(label) for label in list(labels)[:3]]
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"options": trimmed, "ts_ms": int(time.time() * 1000)}
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, target)
    try:
        target.chmod(0o600)
    except OSError:
        pass


def read_options(path: Path | str) -> list[str]:
    target = Path(path)
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, dict):
        return []
    raw = data.get("options")
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw[:3]]


def clear_options(path: Path | str) -> None:
    try:
        Path(path).unlink()
    except FileNotFoundError:
        return
    except OSError:
        pass
