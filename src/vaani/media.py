"""Voice media transport controls (pause/next/prev/seek) via MPRIS + YouTube keys."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Callable


SenderFactory = Callable[[], Any]
logger = logging.getLogger("vaani.media")


@dataclass(frozen=True)
class MediaAction:
    name: str
    method: str  # play|play_pause|pause|stop|next_track|previous_track|seek_forward|seek_backward
    times: int = 1
    spoken: str = ""


# Whole-utterance STT near-misses for short snap commands (live failures).
_SHORT_MEDIA_ALIASES = {
    "bless you": "play",
    "blessyou": "play",
    "clay": "play",
    "pray": "play",
    "plea": "play",
    "plesa": "play",
    "please": "play",
    "lay": "play",
    "nex": "next",
    "neck": "next",
    "next song": "next song",
    "next track": "next track",
    "skip": "skip",
    "skit": "skip",
}

# Seek before bare "skip" → next.
_SEEK_FORWARD_RE = re.compile(
    r"\b("
    r"skip\s+(?:ahead|forward)|"
    r"(?:seek|jump)\s+forward|"
    r"fast\s*forward|"
    r"(?:skip|forward|seek|jump)\s+(?:a\s+few\s+seconds|\d{1,3}\s*seconds?|\d{1,3}\s*secs?)|"
    r"(?:skip|go)\s+ahead(?:\s+a\s+(?:bit|little))?|"
    r"thoda\s+aage|aage\s+(?:karo|badhao)|"
    r"forward\s+(?:the\s+)?(?:song|track)"
    r")\b",
    re.I,
)
_SEEK_BACK_RE = re.compile(
    r"\b("
    r"skip\s+back(?:ward)?|"
    r"(?:seek|jump)\s+back(?:ward)?|"
    r"rewind|"
    r"(?:go\s+)?back\s+(?:a\s+few\s+seconds|\d{1,3}\s*seconds?|\d{1,3}\s*secs?)|"
    r"(?:skip|rewind|seek)\s+(?:a\s+few\s+seconds|\d{1,3}\s*seconds?)\s+back|"
    r"thoda\s+peeche|peeche\s+(?:karo|le\s*jao)|"
    r"rewind\s+(?:the\s+)?(?:song|track)"
    r")\b",
    re.I,
)
_SECONDS_RE = re.compile(r"\b(\d{1,3})\s*(?:seconds?|secs?)\b", re.I)

_PAUSE_RE = re.compile(
    r"\b("
    r"pause(?:\s+(?:it|this|the))?(?:\s+(?:song|track|music|video|playback))?|"
    r"pause\s+karo|"
    r"rok(?:o| do| dena)?"
    r")\b",
    re.I,
)
_RESUME_RE = re.compile(
    r"\b("
    r"resume(?:\s+(?:it|playback|the\s+song))?|"
    r"unpause|"
    r"continue\s+(?:playing|playback|the\s+song)|"
    r"phir\s+se\s+chalao|phir\s+chalao|"
    r"play\s+(?:again|the\s+song)"  # not "play <title>"
    r")\b",
    re.I,
)
# Bare "play" / "play it" / "play music" → resume, not YouTube search.
_BARE_PLAY_RE = re.compile(
    r"^(?:please\s+)?play(?:\s+(?:it|this|music|playback))?\.?$",
    re.I,
)
_NEXT_RE = re.compile(
    r"\b("
    r"(?:go\s+to\s+)?(?:the\s+)?next(?:\s+(?:song|track|one|gaana|gana))?|"
    r"(?:play\s+)?(?:the\s+)?next(?:\s+(?:song|track|one|gaana|gana))?|"
    r"skip(?:\s+(?:this|the|it))?(?:\s+(?:song|track|one|to\s+next))?|"
    r"skip\s+track|"
    r"agla(?:\s+(?:gana|gaana|song|track))?|"
    r"agli\s+(?:track|song)"
    r")\b",
    re.I,
)
_PREV_RE = re.compile(
    r"\b("
    r"(?:play\s+)?(?:the\s+)?(?:previous|prev|last)(?:\s+(?:song|track|one|gaana|gana))?|"
    r"go\s+back(?:\s+to\s+(?:the\s+)?(?:previous|last)\s+(?:song|track))?|"
    r"pichla(?:\s+(?:gana|gaana|song|track))?|"
    r"pichhli\s+(?:track|song)"
    r")\b",
    re.I,
)
_STOP_RE = re.compile(
    r"\b("
    r"stop(?:\s+(?:the\s+)?)?(?:song|track|music|playback|video|it|this)|"
    r"stop\s+playing|"
    r"music\s+band(?:\s*karo)?|"
    r"gana\s+band(?:\s*karo)?"
    r")\b",
    re.I,
)
_BARE_STOP_RE = re.compile(
    r"^(?:please\s+)?stop(?:\s+(?:it|this))?\.?$",
    re.I,
)
# Bare toggle — only when utterance is basically just this.
_PLAY_PAUSE_ONLY_RE = re.compile(
    r"^(?:please\s+)?(?:play\s*/\s*pause|play\s+pause|toggle\s+playback)\s*$",
    re.I,
)


def normalize_media_utterance(command: str) -> str:
    """Map short STT mishears onto media command words."""
    text = " ".join((command or "").strip().split())
    if not text:
        return ""
    key = text.rstrip(".,!?:;").casefold()
    alias = _SHORT_MEDIA_ALIASES.get(key)
    if alias:
        logger.info(
            "event=media_stt_alias from=%r to=%r",
            text,
            alias,
        )
        return alias
    return text


def _seek_times(text: str) -> int:
    """Map spoken seconds to number of ~10s seek steps."""
    match = _SECONDS_RE.search(text)
    if not match:
        return 1
    seconds = max(1, min(120, int(match.group(1))))
    return max(1, min(12, (seconds + 9) // 10))


def resolve_media_action(command: str) -> MediaAction | None:
    text = normalize_media_utterance(command)
    if not text:
        return None
    folded = text.casefold()

    # Don't steal "play <song title> on youtube" style requests.
    if re.search(r"\bplay\b.+\b(on\s+)?(youtube|spotify|prime|netflix)\b", folded):
        return None
    if re.search(r"\b(play|bajao|chalao)\b.+\b(song|gana|gaana|track|music)\b", folded):
        # "play next song" is ok; "play coldplay song" is not a transport control.
        if not re.search(r"\b(next|previous|prev|last|agla|pichla)\b", folded):
            return None

    if _SEEK_FORWARD_RE.search(text):
        times = _seek_times(text)
        return MediaAction("seek forward", "seek_forward", times=times, spoken=text)
    if _SEEK_BACK_RE.search(text):
        times = _seek_times(text)
        return MediaAction("seek backward", "seek_backward", times=times, spoken=text)
    if _BARE_STOP_RE.match(text) or _STOP_RE.search(text):
        return MediaAction("stop", "stop", spoken=text)
    if _NEXT_RE.search(text):
        return MediaAction("next track", "next_track", spoken=text)
    if _PREV_RE.search(text):
        return MediaAction("previous track", "previous_track", spoken=text)
    if _PLAY_PAUSE_ONLY_RE.match(text):
        return MediaAction("play/pause", "play_pause", spoken=text)
    if _BARE_PLAY_RE.match(text) or _RESUME_RE.search(text):
        # Bare "play" / resume → MPRIS Play (not toggle, not YouTube search).
        return MediaAction("play", "play", spoken=text)
    if _PAUSE_RE.search(text):
        return MediaAction("pause", "pause", spoken=text)
    return None


def _run_via_youtube_cdp(action: MediaAction) -> bool:
    """Drive the YouTube tab through Brave DevTools — works while minimized."""
    try:
        from .platform.linux.brave_cdp import youtube_cdp_action
    except Exception:
        return False
    if action.method == "stop":
        return youtube_cdp_action("pause", times=1)
    return youtube_cdp_action(action.method, times=action.times)


def _run_via_youtube_keys(action: MediaAction) -> bool:
    """Last-resort: focus the YouTube window and tap player shortcuts."""
    try:
        from .platform.linux.youtube_tab import youtube_shortcut
    except Exception:
        return False
    method = action.method
    if method == "next_track":
        return youtube_shortcut("Shift_L", "n")
    if method == "previous_track":
        return youtube_shortcut("Shift_L", "p")
    if method == "play":
        return youtube_shortcut("k")
    if method == "pause":
        return youtube_shortcut("k")
    if method == "play_pause":
        return youtube_shortcut("k")
    if method == "seek_forward":
        ok = False
        for _ in range(action.times):
            ok = youtube_shortcut("l") or ok
        return ok
    if method == "seek_backward":
        ok = False
        for _ in range(action.times):
            ok = youtube_shortcut("j") or ok
        return ok
    return False


def _run_via_mpris(action: MediaAction) -> bool:
    from . import mpris

    method = action.method
    if method == "play":
        return mpris.mpris_play()
    if method == "play_pause":
        return mpris.mpris_play_pause()
    if method == "pause":
        return mpris.mpris_pause()
    if method == "stop":
        return mpris.mpris_stop()
    if method == "next_track":
        return mpris.mpris_next()
    if method == "previous_track":
        return mpris.mpris_previous()
    if method == "seek_forward":
        return mpris.mpris_seek_seconds(10.0 * action.times)
    if method == "seek_backward":
        return mpris.mpris_seek_seconds(-10.0 * action.times)
    return False


def run_media_action(
    action: MediaAction,
    *,
    sender: Any | None = None,
) -> str:
    from .hotkeys import XTestMediaKeySender

    # Order matters:
    # - next/prev: CDP first (Brave Media Session often sets CanGoNext=false,
    #   so MPRIS Next is a silent no-op; keyboard needs the window focused).
    # - play/pause/seek: MPRIS usually works without focus; CDP backs it up.
    ok = False
    if action.method in {"next_track", "previous_track"}:
        ok = (
            _run_via_youtube_cdp(action)
            or _run_via_mpris(action)
            or _run_via_youtube_keys(action)
        )
    elif action.method in {"seek_forward", "seek_backward"}:
        ok = (
            _run_via_mpris(action)
            or _run_via_youtube_cdp(action)
            or _run_via_youtube_keys(action)
        )
    else:
        ok = _run_via_mpris(action)
        if not ok and action.method in {"play", "pause", "play_pause", "stop"}:
            ok = _run_via_youtube_cdp(action) or _run_via_youtube_keys(action)

    if not ok:
        box = sender if sender is not None else XTestMediaKeySender()
        # Map dedicated "play" onto play_pause for XF86 fallback.
        method_name = "play_pause" if action.method == "play" else action.method
        method = getattr(box, method_name, None)
        if not callable(method):
            return f"Unable to {action.name}."
        try:
            if action.method in {"seek_forward", "seek_backward"}:
                tap = getattr(box, "_tap_xf86", None)
                if callable(tap):
                    attr = (
                        "XK_XF86_AudioForward"
                        if action.method == "seek_forward"
                        else "XK_XF86_AudioRewind"
                    )
                    tap(attr, times=action.times)
                else:
                    for _ in range(action.times):
                        method()
            else:
                method()
            ok = True
        except Exception:
            return f"Unable to {action.name}."

    if action.method.startswith("seek_"):
        approx = action.times * 10
        return f"{action.name.capitalize()} (~{approx}s)."
    labels = {
        "pause": "Paused.",
        "stop": "Stopped.",
        "next_track": "Next track.",
        "previous_track": "Previous track.",
        "play_pause": "Toggled playback.",
        "play": "Playing.",
    }
    return labels.get(action.method, f"{action.name.capitalize()}.")
