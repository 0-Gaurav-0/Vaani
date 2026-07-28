"""Devanagari → rough Latin Hinglish (spoken paste, not scholarly IAST)."""
from __future__ import annotations

import re

# Independent vowels
_VOWELS = {
    "अ": "a",
    "आ": "aa",
    "इ": "i",
    "ई": "ee",
    "उ": "u",
    "ऊ": "oo",
    "ए": "e",
    "ऐ": "ai",
    "ओ": "o",
    "औ": "au",
    "ऋ": "ri",
}

# Consonants (inherent 'a' unless matra / virama follows)
_CONS = {
    "क": "k",
    "ख": "kh",
    "ग": "g",
    "घ": "gh",
    "ङ": "ng",
    "च": "ch",
    "छ": "chh",
    "ज": "j",
    "झ": "jh",
    "ञ": "ny",
    "ट": "t",
    "ठ": "th",
    "ड": "d",
    "ढ": "dh",
    "ण": "n",
    "त": "t",
    "थ": "th",
    "द": "d",
    "ध": "dh",
    "न": "n",
    "प": "p",
    "फ": "ph",
    "ब": "b",
    "भ": "bh",
    "म": "m",
    "य": "y",
    "र": "r",
    "ल": "l",
    "व": "v",
    "श": "sh",
    "ष": "sh",
    "स": "s",
    "ह": "h",
    "क्ष": "ksh",
    "त्र": "tr",
    "ज्ञ": "gy",
    "ड़": "d",
    "ढ़": "dh",
    "फ़": "f",
    "ज़": "z",
    "ख़": "kh",
    "ग़": "g",
}

_MATRA = {
    "ा": "aa",
    "ि": "i",
    "ी": "ee",
    "ु": "u",
    "ू": "oo",
    "े": "e",
    "ै": "ai",
    "ो": "o",
    "ौ": "au",
    "ृ": "ri",
    "ं": "n",
    "ँ": "n",
    "ः": "h",
}

_VIRAMA = "्"
_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]+")


def has_devanagari(text: str) -> bool:
    return bool(_DEVANAGARI_RE.search(text or ""))


def romanize_devanagari(text: str) -> str:
    """Romanize Devanagari runs; leave Latin/punctuation unchanged."""
    if not text:
        return ""
    if not has_devanagari(text):
        return text

    def _romanize_run(run: str) -> str:
        out: list[str] = []
        i = 0
        n = len(run)
        while i < n:
            # Two-char conjuncts / nukta forms first
            dig = run[i : i + 2]
            if dig in _CONS:
                cons = _CONS[dig]
                i += 2
            elif run[i] in _VOWELS:
                out.append(_VOWELS[run[i]])
                i += 1
                continue
            elif run[i] in _CONS:
                cons = _CONS[run[i]]
                i += 1
            elif run[i] in _MATRA:
                out.append(_MATRA[run[i]])
                i += 1
                continue
            else:
                # Skip unknown mark
                i += 1
                continue

            # After consonant: matra, virama, or inherent a
            if i < n and run[i] == _VIRAMA:
                out.append(cons)
                i += 1
                continue
            if i < n and run[i] in _MATRA:
                # anusvara/visarga attach after vowel matra handling
                mat = run[i]
                i += 1
                if mat in {"ं", "ँ", "ः"}:
                    out.append(cons + "a" + _MATRA[mat])
                else:
                    out.append(cons + _MATRA[mat])
                # trailing anusvara after matra
                if i < n and run[i] in {"ं", "ँ"}:
                    out.append(_MATRA[run[i]])
                    i += 1
                continue
            out.append(cons + "a")
        return "".join(out)

    return _DEVANAGARI_RE.sub(lambda m: _romanize_run(m.group(0)), text)
