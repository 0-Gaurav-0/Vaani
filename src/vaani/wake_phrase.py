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
        "fani",
        "bani",
        "waniy",
        "vanii",
        "vauney",
        "waney",
        "barney",  # Whisper often hears Vaani → Barney
        "barnie",
        "varney",
    }
)

_WAKE_PREFIX = (
    r"(?:hey|hi|hello|ok|okay|yo|oye|arey|are|sun|ei|ay|awaken|wake)"
)


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
    t = re.sub(r"[^a-z0-9]", "", (token or "").casefold())
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
    if len(tokens) < 2:
        return None
    # Find prefix + name near the start (allow one junk token before hey).
    start = 0
    if tokens and tokens[0].casefold() in {"um", "uh", "ah", "erm", "like", "so"}:
        start = 1
    if start >= len(tokens):
        return None
    prefix = tokens[start].casefold()
    if not re.fullmatch(_WAKE_PREFIX, prefix, flags=re.I):
        # "Vaani, play …" without hey — only if clearly addressed.
        if wake_name_matches(tokens[start]) and start + 1 < len(tokens):
            rest_tokens = tokens[start + 1 :]
            # Skip punctuation after bare name: "Vaani, play…"
            while rest_tokens and re.fullmatch(r"[.,!?:;]+", rest_tokens[0]):
                rest_tokens = rest_tokens[1:]
            payload = _tokens_to_payload(rest_tokens)
            return payload
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
