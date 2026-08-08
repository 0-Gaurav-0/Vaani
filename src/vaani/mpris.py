"""Control browser/media players via MPRIS (D-Bus) — works when XF86 keys do not."""
from __future__ import annotations

import logging
import re
import subprocess
from typing import Iterable

logger = logging.getLogger("vaani.mpris")

_PLAYER_RE = re.compile(r"^org\.mpris\.MediaPlayer2\.\S+")


def list_mpris_players() -> list[str]:
    try:
        out = subprocess.check_output(
            ["busctl", "--user", "list"],
            text=True,
            timeout=2.0,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    names: list[str] = []
    for line in out.splitlines():
        name = line.split(None, 1)[0] if line.strip() else ""
        if _PLAYER_RE.match(name):
            names.append(name)
    return names


def _call(player: str, method: str, *args: str) -> bool:
    cmd = [
        "busctl",
        "--user",
        "call",
        player,
        "/org/mpris/MediaPlayer2",
        "org.mpris.MediaPlayer2.Player",
        method,
        *args,
    ]
    try:
        subprocess.run(
            cmd,
            check=True,
            timeout=2.0,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def _get_bool(player: str, prop: str) -> bool | None:
    try:
        out = subprocess.check_output(
            [
                "busctl",
                "--user",
                "get-property",
                player,
                "/org/mpris/MediaPlayer2",
                "org.mpris.MediaPlayer2.Player",
                prop,
            ],
            text=True,
            timeout=2.0,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    # e.g. `b true` / `b false`
    return "true" in out.casefold()


def _call_any(players: Iterable[str], method: str, *args: str) -> bool:
    ok = False
    for player in players:
        if method == "Next" and _get_bool(player, "CanGoNext") is False:
            logger.info(
                "event=mpris_skip player=%s method=Next reason=CanGoNext_false",
                player,
            )
            continue
        if method == "Previous" and _get_bool(player, "CanGoPrevious") is False:
            logger.info(
                "event=mpris_skip player=%s method=Previous reason=CanGoPrevious_false",
                player,
            )
            continue
        if _call(player, method, *args):
            ok = True
            logger.info("event=mpris_call player=%s method=%s", player, method)
    return ok


def mpris_available() -> bool:
    return bool(list_mpris_players())


def mpris_play() -> bool:
    return _call_any(list_mpris_players(), "Play")


def mpris_pause() -> bool:
    return _call_any(list_mpris_players(), "Pause")


def mpris_stop() -> bool:
    return _call_any(list_mpris_players(), "Stop")


def mpris_play_pause() -> bool:
    return _call_any(list_mpris_players(), "PlayPause")


def mpris_next() -> bool:
    return _call_any(list_mpris_players(), "Next")


def mpris_previous() -> bool:
    return _call_any(list_mpris_players(), "Previous")


def mpris_seek_seconds(delta: float) -> bool:
    """Seek by ``delta`` seconds (negative = rewind)."""
    micros = int(delta * 1_000_000)
    return _call_any(list_mpris_players(), "Seek", "x", str(micros))


def mpris_pause_all() -> bool:
    """Pause every MPRIS player (stops dual-tab audio before a new play)."""
    return mpris_pause()
