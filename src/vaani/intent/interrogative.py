"""Interrogative detection for act-mode safety (T2.5 / parallel-agents §0).

Guide/screen-offer is deferred. Interrogatives that resolve to mutating verbs
(R2+) refuse with a short "say it as a command" nudge — never execute, never
stage confirm, never open guide.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from vaani.intent.schema import Intent, Result, RiskClass, Status, Verb

# R2+ = mutating / confirm-gated (same set as policy.confirm.requires_confirm).
MUTATING_RISKS = frozenset({RiskClass.R2, RiskClass.R3, RiskClass.R4})

# Leading cues only (spec §1.2). Mid-utterance "what's" must not trip this
# (e.g. "show what's using the most CPU").
_LEAD_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(rf"^{pat}\b")
    for pat in (
        r"how do (?:i|you|we)",
        r"how (?:can|do|would|should) (?:i|you|we)",
        r"what(?:'s| is| are)",
        r"where(?:'s| is| are)",
        r"why(?:'s| is| are| do| does| can| would)?",
        r"which",
        r"can (?:you|i|we)",
        r"could (?:you|i|we)",
        r"would you",
        r"is there",
        r"do (?:i|you|we)",
        r"who(?:'s| is)",
    )
)

_LEAD_STRIP = re.compile(
    r"^(?:"
    r"how do (?:i|you|we)|"
    r"how (?:can|do|would|should) (?:i|you|we)|"
    r"what(?:'s| is| are)|"
    r"where(?:'s| is| are)|"
    r"why(?:'s| is| are| do| does| can| would)?|"
    r"which|"
    r"can (?:you|i|we)|"
    r"could (?:you|i|we)|"
    r"would you|"
    r"is there|"
    r"do (?:i|you|we)|"
    r"who(?:'s| is)"
    r")\b[\s,:\-—–]*"
)

# After stripping a lead, port queries often look like "on port 3000".
_PORT_QUERY_REST = re.compile(
    r"^(?:(?:on|using)\s+)?port\s+(\d{1,5})\b"
)


def is_interrogative(text: str) -> bool:
    """True when ``text`` leads with an interrogative cue."""
    folded = _fold(text)
    if not folded:
        return False
    return any(pat.search(folded) for pat in _LEAD_PATTERNS)


def strip_interrogative_lead(text: str) -> str:
    """Remove a leading interrogative cue for grammar matching."""
    folded = _fold(text)
    if not folded:
        return ""
    stripped = _LEAD_STRIP.sub("", folded, count=1)
    return " ".join(stripped.split())


def port_query_slots(text: str) -> dict[str, Any] | None:
    """Slots for interrogative port queries like \"what's on port 3000\"."""
    rest = strip_interrogative_lead(text) if is_interrogative(text) else _fold(text)
    matched = _PORT_QUERY_REST.match(rest)
    if matched is None:
        return None
    try:
        port = int(matched.group(1))
    except ValueError:
        return None
    if not 1 <= port <= 65535:
        return None
    return {"port": port, "signal": "term"}


def suggest_command(verb_name: str, slots: Mapping[str, Any]) -> str:
    """Short imperative phrasing the user can speak instead."""
    if verb_name == "system.port.free":
        port = slots.get("port")
        return f"free port {port}" if port is not None else "free port <n>"
    if verb_name == "system.proc.kill":
        name = slots.get("name")
        return (
            f"kill the process named {name}"
            if name
            else "kill the process named <name>"
        )
    if verb_name == "system.trash.empty":
        return "empty the trash"
    if verb_name == "system.wifi.set":
        return "turn on Wi-Fi" if slots.get("enabled") else "turn off Wi-Fi"
    if verb_name == "app.quit":
        name = slots.get("name")
        return f"quit {name}" if name else "quit <app>"
    # Generic fallback from verb title-ish name.
    return verb_name.replace(".", " ")


def should_refuse_interrogative(verb: Verb, intent: Intent) -> bool:
    """Mutating/action verbs (R2+) must not run on interrogative speech."""
    return "interrogative" in intent.modifiers and verb.risk in MUTATING_RISKS


def refuse_interrogative(verb: Verb, intent: Intent) -> Result:
    """Build a short REFUSED result with a command suggestion."""
    suggestion = suggest_command(verb.name, intent.slots)
    summary = f"Say it as a command — try: {suggestion}"
    return Result(
        status=Status.REFUSED,
        summary=summary,
        detail=summary,
        evidence=(suggestion,) if suggestion else (),
        rung=verb.rung,
    )


def _fold(text: str) -> str:
    if not text or not text.strip():
        return ""
    return " ".join(text.casefold().strip().split())
