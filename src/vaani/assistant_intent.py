"""Classify assistant utterances: action vs question vs paste."""
from __future__ import annotations

import re

_ACTION_PATTERNS = (
    r"\b(open|launch|start|play|watch|search|run|visit|browse|surf|navigate)\b",
    r"\b(kholo|khol|chalao|chalu|shuru|dikhao|dikha|jao|chalo|bajao|baja|suno)\b",
    r"\b(gaana|gana|song|music|trailer)\b",
    r"\byoutube\b",
    r"\bskill\b",
    # Whisper often clips "play" → "ple" at the start of short holds.
    r"^ple\b",
)

_AGENT_PATTERNS = (
    r"\bfix\b",
    r"\brefactor\b",
    r"\bimplement\b",
    r"\bdebug\b",
    r"\bwrite (a |the )?(script|function|class|test|code)\b",
    r"\brun (the )?tests?\b",
)

_QUESTION_PATTERNS = (
    r"^(who|what|when|where|why|how|which|whom|whose)\b",
    r"\b(who is|what is|what are|when is|where is|why is|how many|how much)\b",
    r"\b(what should|which .+ (should|do|is|are))\b",
    r"\b(kaun|kya|kab|kahan|kyun|kaise|kis|kons[aei])\b",
)

# Opinion / help asks that often omit "?" in speech transcripts.
_QA_REQUEST_PATTERNS = (
    r"\b(recommend|recommendation|suggest|suggestion|advise|advise me)\b",
    r"\b(can you|could you|would you|will you|do you know)\b",
    r"\b(should i|any (good|best)|looking for|help me)\b",
    r"\b(give me|show me|find me|tell me)\b",
    r"\b(best|top)\b.+\b(manga|anime|movie|book|song|show|series|app|tool|restaurant|place)\b",
    r"\b(batao|bataao|suggest karo|recommend karo)\b",
)

_PASTE_HINTS = (
    r"^(please )?(type|paste|write|insert|dictate)\b",
    r"\b(send this|email this|message this|paste this|type this)\b",
    r"\bplease send this to\b",
)


def looks_like_action(text: str) -> bool:
    normalized = " ".join((text or "").casefold().split())
    if not normalized:
        return False
    return any(re.search(p, normalized) for p in _ACTION_PATTERNS)


def classify_assistant_intent(text: str) -> str:
    """Return ``action``, ``qa``, ``codex``, or ``paste``."""
    normalized = " ".join((text or "").casefold().split())
    if not normalized:
        return "paste"
    if looks_like_action(normalized):
        return "action"
    for pattern in _AGENT_PATTERNS:
        if re.search(pattern, normalized):
            return "codex"
    for pattern in _PASTE_HINTS:
        if re.search(pattern, normalized):
            return "paste"
    if normalized.endswith("?"):
        return "qa"
    for pattern in _QUESTION_PATTERNS:
        if re.search(pattern, normalized):
            return "qa"
    for pattern in _QA_REQUEST_PATTERNS:
        if re.search(pattern, normalized):
            return "qa"
    return "paste"
