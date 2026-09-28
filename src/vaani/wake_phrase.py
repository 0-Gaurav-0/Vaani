"""Wake-phrase matching for hands-free assistant: “hey Vaani …”.

STT mangles the brand constantly (Vani/Wani/Vernie/…). Match known aliases and
near-miss spellings so wake does not depend on exact letters.
"""
from __future__ import annotations

import re

# Shared with agent-handoff mangling, plus extra Whisper/Hinglish misses.
_WAKE_NAME_ALIASES = frozenset(
    {
        "vaani",
        "vani",
        "wani",
        "wanni",
        "wahni",
        "vahni",
        "vaany",
        "vaanee",
        "vaanii",
        "waani",
        "whani",
        "vernie",
        "verni",
        "varni",
        "vonnie",
        "bonnie",  # occasional Whisper miss
        "bunny",  # hey Vaani → hey bunny
        "bunnie",
        "honey",  # hey Vaani → hey honey
        "honney",
        "fani",
        "bani",
        "rami",  # accent / STT miss for Vaani
        "rani",
        "waniy",
        "vanii",
        "vauney",
        "waney",
        "vahey",  # hey Vaani → hey Vahey
        "vahe",
        "vahay",
        "onee",  # hey Vaani → hey onee
        "oni",
        "onni",
        "paanee",  # hinglish STT for Vaani
        "pani",
        "panee",
        "barney",  # Whisper often hears Vaani → Barney
        "barnie",
        "varney",
        "vanny",
        "vannie",
        "waniie",
        "warning",  # STT often hears Vaani → warning
        "warren",
        "waniya",
    }
)

# Deliberate wake openers only — short fillers like "a"/"eh" caused false wakes.
_WAKE_PREFIX = r"(?:hey|hi|hello|ok|okay|yo|oye|hai)"

# Bare “Vaani” alone: only clear brand spellings (not bunny/barney — those need hey).
_BARE_WAKE_NAMES = frozenset(
    {
        "vaani",
        "vani",
        "wani",
        "wanni",
        "wahni",
        "vahni",
        "vaany",
        "vaanee",
        "vaanii",
        "waani",
        "whani",
        "vanii",
        "vanny",
        "vannie",
        "bani",
        "fani",
        "vahey",
        "vahe",
    }
)


def _normalize_name_token(token: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (token or "").casefold())


def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        for j, cb in enumerate(b, start=1):
            ins = cur[j - 1] + 1
            delete = prev[j] + 1
            sub = prev[j - 1] + (0 if ca == cb else 1)
            cur.append(min(ins, delete, sub))
        prev = cur
    return prev[-1]


def wake_name_matches(token: str) -> bool:
    """True if ``token`` is Vaani / Hermes under common STT spellings."""
    t = _normalize_name_token(token)
    if not t or len(t) < 3:
        return False
    if t in _WAKE_NAME_ALIASES or t == "hermes":
        return True
    # Near-miss to the short brand forms (accent / Whisper noise).
    for canon in ("vaani", "vani", "wani", "hermes"):
        dist = _levenshtein(t, canon)
        # Allow 2 edits for longer names, 1 for very short.
        limit = 2 if len(canon) >= 5 else 1
        if dist <= limit:
            return True
    return False


def bare_wake_name_matches(token: str) -> bool:
    """True if bare ``token`` alone should wake (no hey/okay)."""
    t = _normalize_name_token(token)
    if not t or len(t) < 3:
        return False
    if t in _BARE_WAKE_NAMES:
        return True
    for canon in ("vaani", "vani", "wani"):
        dist = _levenshtein(t, canon)
        limit = 2 if len(canon) >= 5 else 1
        if dist <= limit:
            return True
    return False


def extract_wake_assistant(text: str) -> str | None:
    """If utterance is a wake, return the command payload (may be empty).

    Returns ``None`` when this is not a wake phrase.
    """
    normalized = " ".join((text or "").strip().split())
    if not normalized:
        return None
    # Strip leading filler STT often inserts.
    cleaned = re.sub(
        r"^(?:um+|uh+|ah+|erm+|like)\s+",
        "",
        normalized,
        flags=re.I,
    )
    tokens = re.findall(r"[A-Za-z0-9']+|[.,!?:;]+", cleaned)
    if not tokens:
        return None
    # Fused forms Whisper emits: "Hivani", "HeyVaani", "Okayvani".
    fused = _extract_fused_wake(tokens)
    if fused is not None:
        return fused
    # Find prefix + name near the start (allow one junk token before hey).
    start = 0
    if tokens and tokens[0].casefold() in {"um", "uh", "ah", "erm", "like", "so"}:
        start = 1
    if start >= len(tokens):
        return None
    prefix = tokens[start].casefold()
    if not re.fullmatch(_WAKE_PREFIX, prefix, flags=re.I):
        # Bare “Vaani” / “Vaani.” / “Vaani play …” (clear brand forms only).
        if bare_wake_name_matches(tokens[start]):
            rest_tokens = tokens[start + 1 :]
            while rest_tokens and re.fullmatch(r"[.,!?:;]+", rest_tokens[0]):
                rest_tokens = rest_tokens[1:]
            return _tokens_to_payload(rest_tokens)
        return None
    # Skip punctuation between prefix and name: "Hey, Vani!"
    name_i = start + 1
    while name_i < len(tokens) and re.fullmatch(r"[.,!?:;]+", tokens[name_i]):
        name_i += 1
    if name_i >= len(tokens):
        return None
    name = tokens[name_i]
    if not wake_name_matches(name):
        return None
    rest_tokens = tokens[name_i + 1 :]
    # Drop trailing punctuation / name echoes: "hey vaani vaani" / "Hey, Vani!"
    while rest_tokens and (
        wake_name_matches(rest_tokens[0])
        or re.fullmatch(r"[.,!?:;]+", rest_tokens[0])
    ):
        rest_tokens = rest_tokens[1:]
    return _tokens_to_payload(rest_tokens)


def _extract_fused_wake(tokens: list[str]) -> str | None:
    """Handle single-token wakes like ``Hivani`` / ``HeyVaani``."""
    word_tokens = [t for t in tokens if not re.fullmatch(r"[.,!?:;]+", t)]
    if not word_tokens:
        return None
    first = _normalize_name_token(word_tokens[0])
    prefixes = ("hey", "hello", "okay", "ok", "hi", "oye", "hai", "yo")
    for pref in prefixes:
        if not first.startswith(pref) or len(first) <= len(pref) + 2:
            continue
        rest = first[len(pref) :]
        if bare_wake_name_matches(rest) or wake_name_matches(rest):
            payload_tokens = word_tokens[1:]
            return _tokens_to_payload(payload_tokens)
    return None


def _tokens_to_payload(tokens: list[str]) -> str:
    if not tokens:
        return ""
    parts: list[str] = []
    for tok in tokens:
        if re.fullmatch(r"[.,!?:;]+", tok):
            if parts:
                parts[-1] = parts[-1] + tok[0]
            continue
        parts.append(tok)
    return " ".join(parts).strip(" .,!?:;")


# Whisper often invents these from silence / room noise — never a wake.
_WAKE_HALLUCINATIONS = frozenset(
    {
        "thank you",
        "thanks",
        "thanks for watching",
        "thank you for watching",
        "bye",
        "goodbye",
        "you",
        "the",
        "a",
        "um",
        "uh",
        "hmm",
        "mm",
        "okay",
        "ok",
        "yes",
        "yeah",
        "no",
        "i'm sorry",
        "im sorry",
        "oh my god",
        "bang",
    }
)


def is_wake_hallucination(text: str) -> bool:
    """True for empty/noise transcripts that should never trigger wake matching."""
    cleaned = " ".join((text or "").strip().casefold().split())
    cleaned = re.sub(r"[.!?,;:]+$", "", cleaned).strip()
    if not cleaned:
        return True
    if cleaned in _WAKE_HALLUCINATIONS:
        return True
    # Single tiny token that's not a Vaani name.
    tokens = re.findall(r"[a-z0-9']+", cleaned)
    if len(tokens) == 1 and not wake_name_matches(tokens[0]):
        return True
    return False


def normalize_wake_transcript(text: str) -> str:
    """Romanize Devanagari wake transcripts so matching stays Latin-only."""
    raw = (text or "").strip()
    if not raw:
        return ""
    try:
        from .romanize import has_devanagari, romanize_devanagari

        if has_devanagari(raw):
            return romanize_devanagari(raw).strip() or raw
    except Exception:
        pass
    return raw
