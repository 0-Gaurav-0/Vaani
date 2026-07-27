"""Utterance normalization for intent matching.

Operates on a *matching* copy only. Callers must keep the raw transcript for
history/storage and never feed this output back as the stored utterance.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vaani.intent.lexicon import Lexicon

# Longer phrases first so "just show me the command" wins over "just show me".
_DRY_RUN_PHRASES: tuple[str, ...] = (
    "don't run it",
    "dont run it",
    "don’t run it",
    "just show me the command",
    "just show me",
    "what would you run",
)

_FILLER_PHRASES: tuple[str, ...] = (
    "can you please",
    "could you please",
    "would you please",
    "can you",
    "could you",
    "would you",
    "please",
    "for me",
    "for us",
    "umm",
    "uhh",
    "ahh",
    "hmm",
    "um",
    "uh",
    "ah",
)

_SEPARATORS: dict[str, str] = {
    "slash": "/",
    "backslash": "\\",
    "dash": "-",
    "hyphen": "-",
    "minus": "-",
    "underscore": "_",
    "dot": ".",
    "period": ".",
    "point": ".",
}

_ONES: dict[str, int] = {
    "zero": 0,
    "oh": 0,
    "o": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
}

_TEENS: dict[str, int] = {
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
}

_TENS: dict[str, int] = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}

_SCALES: dict[str, int] = {
    "hundred": 100,
    "thousand": 1000,
    "million": 1_000_000,
}

_NUMBER_WORDS: frozenset[str] = frozenset(
    set(_ONES) | set(_TEENS) | set(_TENS) | set(_SCALES) | {"and"}
)

_PORT_KEYWORD = re.compile(r"(?<!\w)port\s+(\d{1,5})(?!\w)")
_BARE_PORT = re.compile(r"^\d{2,5}$")
_LOCALHOST_NUMBER = re.compile(r"(?<!\w)localhost\s+(\d{1,5})(?!\w)")


def detect_modifiers(text: str) -> frozenset[str]:
    """Return intent modifiers present in ``text`` (e.g. ``dry_run``)."""
    if not text or not text.strip():
        return frozenset()
    folded = " ".join(text.casefold().strip().split())
    for phrase in _DRY_RUN_PHRASES:
        if re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", folded):
            return frozenset({"dry_run"})
    return frozenset()


def normalize(text: str, *, lexicon: Lexicon | None = None) -> str:
    """Casefold, strip fillers/dry-run cues, expand separators/numbers, apply lexicon.

    Safe to call more than once (idempotent for a fixed lexicon). Dry-run cue
    phrases are removed from the matching copy; callers that need the modifier
    should call :func:`detect_modifiers` on the raw transcript.
    """
    from vaani.intent.lexicon import Lexicon as LexiconCls

    if not text or not text.strip():
        return ""

    lex = lexicon if lexicon is not None else LexiconCls.builtin()
    result = " ".join(text.casefold().strip().split())
    # Drop trailing sentence punctuation from ASR ("Open Chrome for me.").
    result = result.strip(".,!?…")
    result = _strip_dry_run_phrases(result)
    result = _strip_fillers(result)
    result = _replace_separators(result)
    result = _replace_number_words(result)
    result = re.sub(r"\b(\d+)\s+percent\b", r"\1", result)
    result = lex.apply(result)
    return " ".join(result.split())


def _strip_dry_run_phrases(text: str) -> str:
    result = f" {text} "
    for phrase in _DRY_RUN_PHRASES:
        result = re.sub(rf"(?<!\w){re.escape(phrase)}(?!\w)", " ", result)
    # Drop leftover dash separators left by "don't run it — just show me …".
    result = re.sub(r"\s[—–]\s", " ", result)
    return " ".join(result.split())


def port_slot(text: str, *, lexicon: Lexicon | None = None) -> int | None:
    """Return a TCP port for port-free style utterances, else ``None``.

    SYS-PORT-01 negative case: ``open localhost 3000`` must not yield a bare
    port slot even though digits are present.
    """
    normalized = normalize(text, lexicon=lexicon)
    if not normalized:
        return None

    # Host:port opens are never bare port slots.
    if _LOCALHOST_NUMBER.search(normalized) and "port" not in normalized:
        return None

    if _BARE_PORT.fullmatch(normalized):
        return _valid_port(normalized)

    keyword = _PORT_KEYWORD.search(normalized)
    if keyword is None:
        return None
    return _valid_port(keyword.group(1))


def _valid_port(raw: str) -> int | None:
    try:
        value = int(raw)
    except ValueError:
        return None
    if 1 <= value <= 65535:
        return value
    return None


def _strip_fillers(text: str) -> str:
    result = f" {text} "
    for phrase in _FILLER_PHRASES:
        if phrase in ("can you", "would you"):
            # Preserve "how can/would you …" for guide.offer grammar hits.
            result = re.sub(
                rf"(?<!how )(?<!\w){re.escape(phrase)}(?!\w)",
                " ",
                result,
            )
        else:
            result = re.sub(rf"(?<!\w){re.escape(phrase)}(?!\w)", " ", result)
    return " ".join(result.split())


def _replace_separators(text: str) -> str:
    tokens = text.split()
    if not tokens:
        return text
    out: list[str] = []
    for token in tokens:
        sep = _SEPARATORS.get(token)
        if sep is not None:
            if out:
                out[-1] = out[-1] + sep
            else:
                out.append(sep)
            continue
        if out and out[-1][-1:] in set(_SEPARATORS.values()):
            out[-1] = out[-1] + token
        else:
            out.append(token)
    return " ".join(out)


def _replace_number_words(text: str) -> str:
    tokens = text.split()
    if not tokens:
        return text
    out: list[str] = []
    i = 0
    while i < len(tokens):
        if tokens[i] not in _NUMBER_WORDS or tokens[i] == "and":
            out.append(tokens[i])
            i += 1
            continue

        digit_run = _take_digit_run(tokens, i)
        if digit_run is not None:
            digits, end = digit_run
            out.append(digits)
            i = end
            continue

        parsed = _take_english_number(tokens, i)
        if parsed is None:
            out.append(tokens[i])
            i += 1
            continue

        value, end = parsed
        # "eighty eighty" → 8080 (two adjacent simple 10–99 groups).
        if (
            10 <= value <= 99
            and _is_simple_span(tokens, i, end)
            and end < len(tokens)
        ):
            nxt = _take_english_number(tokens, end)
            if (
                nxt is not None
                and 10 <= nxt[0] <= 99
                and _is_simple_span(tokens, end, nxt[1])
            ):
                out.append(f"{value}{nxt[0]}")
                i = nxt[1]
                continue

        out.append(str(value))
        i = end
    return " ".join(out)


def _is_simple_span(tokens: list[str], start: int, end: int) -> bool:
    span = tokens[start:end]
    if not span or any(tok in _SCALES for tok in span):
        return False
    return all(tok in _NUMBER_WORDS and tok != "and" for tok in span)


def _take_digit_run(tokens: list[str], start: int) -> tuple[str, int] | None:
    """Parse a run of two+ single-digit words (``three zero zero zero`` → ``3000``)."""
    digits: list[str] = []
    i = start
    while i < len(tokens):
        tok = tokens[i]
        if tok == "and" and digits:
            i += 1
            continue
        if tok in _ONES:
            digits.append(str(_ONES[tok]))
            i += 1
            continue
        break
    if len(digits) < 2 or len(digits) > 6:
        return None
    return "".join(digits), i


def _take_english_number(tokens: list[str], start: int) -> tuple[int, int] | None:
    """Parse one English number: three / twenty three / three thousand / …"""
    if start >= len(tokens) or tokens[start] not in _NUMBER_WORDS or tokens[start] == "and":
        return None

    total = 0
    current = 0
    i = start
    # Track the last magnitude unit so "eighty eighty" does not become 160.
    last: str | None = None
    while i < len(tokens):
        tok = tokens[i]
        if tok == "and":
            if last is None:
                break
            i += 1
            continue
        if tok in _ONES:
            if last in {"ones", "teens"}:
                break
            current += _ONES[tok]
            last = "ones"
            i += 1
            continue
        if tok in _TEENS:
            if last in {"ones", "teens", "tens"}:
                break
            current += _TEENS[tok]
            last = "teens"
            i += 1
            continue
        if tok in _TENS:
            if last in {"ones", "teens", "tens"}:
                break
            current += _TENS[tok]
            last = "tens"
            i += 1
            continue
        if tok in _SCALES:
            scale = _SCALES[tok]
            if last is None and scale >= 100:
                break
            if scale == 100:
                current = (current or 1) * 100
                last = "hundred"
            else:
                total += (current or 1) * scale
                current = 0
                last = "scale"
            i += 1
            continue
        break

    if last is None:
        return None
    return total + current, i
