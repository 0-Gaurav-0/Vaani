"""Allowlisted mute/volume controls via pactl (no shell)."""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable, Sequence


Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class VolumeAction:
    name: str
    argv: tuple[str, ...]


_VOLUME_SET_RE = re.compile(
    r"\b(?:set\s+)?volume\s+(?:to\s+)?(\d{1,3})\s*%?\b",
    re.I,
)
_VOLUME_UP_RE = re.compile(
    r"\b(volume\s+up|increase\s+volume|turn\s+(?:it\s+)?up|"
    r"awaz\s+badhao|volume\s+badhao)\b",
    re.I,
)
_VOLUME_DOWN_RE = re.compile(
    r"\b(volume\s+down|decrease\s+volume|turn\s+(?:it\s+)?down|quieter|"
    r"awaz\s+kam(?:\s*karo)?|volume\s+kam)\b",
    re.I,
)
_UNMUTE_RE = re.compile(r"\b(unmute|awaz\s+chalu)\b", re.I)
_MUTE_RE = re.compile(r"\b(mute(?:\s+volume)?|silence(?:\s+audio)?|awaz\s+band(?:\s*karo)?)\b", re.I)


def resolve_volume_action(command: str) -> VolumeAction | None:
    text = " ".join((command or "").casefold().strip().split())
    if not text:
        return None
    set_match = _VOLUME_SET_RE.search(text)
    if set_match:
        level = min(150, max(0, int(set_match.group(1))))
        return VolumeAction(
            f"volume {level}%",
            ("set-sink-volume", "@DEFAULT_SINK@", f"{level}%"),
        )
    if _UNMUTE_RE.search(text):
        return VolumeAction("unmute", ("set-sink-mute", "@DEFAULT_SINK@", "0"))
    if _MUTE_RE.search(text):
        return VolumeAction("mute", ("set-sink-mute", "@DEFAULT_SINK@", "1"))
    if _VOLUME_UP_RE.search(text):
        return VolumeAction("volume up", ("set-sink-volume", "@DEFAULT_SINK@", "+5%"))
    if _VOLUME_DOWN_RE.search(text):
        return VolumeAction("volume down", ("set-sink-volume", "@DEFAULT_SINK@", "-5%"))
    return None


def _default_runner(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )


def run_volume_action(
    action: VolumeAction,
    *,
    runner: Runner | None = None,
    pactl: str | None = None,
) -> str:
    exe = pactl or shutil.which("pactl")
    if not exe:
        return f"Unable to {action.name}: pactl not found."
    run = runner or _default_runner
    result = run([exe, *action.argv])
    if result.returncode != 0:
        return f"Unable to {action.name}."
    if action.name == "mute":
        return "Muted."
    if action.name == "unmute":
        return "Unmuted."
    return f"Set {action.name}."
