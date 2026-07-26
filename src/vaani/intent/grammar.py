"""Declarative phrase grammar — data-first, ``re`` only."""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from vaani.intent.normalize import normalize


@dataclass(frozen=True)
class SlotRule:
    """Extract or assign a slot when a pattern matches."""

    name: str
    value: Any = None
    """Constant slot value when set."""
    from_group: int | None = None
    """Copy the matched phrase from ``any_of[from_group]``."""
    regex: str | None = None
    """Optional capture pattern applied to the normalized utterance."""


@dataclass(frozen=True)
class Pattern:
    """One declarative match rule for a verb.

    ``any_of``: each inner group needs at least one phrase hit.
    ``require``: all phrases must appear.
    ``exclude``: any hit kills the match.
    ``exact``: when True, the full normalized utterance must equal one phrase
    from the sole ``any_of`` group (used for browser allowlists).
    ``priority``: higher wins on conflict (spec §4.2).
    """

    verb: str
    any_of: tuple[tuple[str, ...], ...] = ()
    require: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    slots: tuple[SlotRule, ...] = ()
    priority: int = 0
    exact: bool = False
    fixed_slots: Mapping[str, Any] = field(default_factory=dict)


def _contains_phrase(text: str, phrase: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text) is not None


def _group_hit(text: str, group: tuple[str, ...]) -> str | None:
    for phrase in group:
        if _contains_phrase(text, phrase):
            return phrase
    return None


def _extract_slots(
    text: str,
    pattern: Pattern,
    group_hits: tuple[str | None, ...],
) -> dict[str, Any]:
    slots: dict[str, Any] = dict(pattern.fixed_slots)
    for rule in pattern.slots:
        if rule.value is not None:
            slots[rule.name] = rule.value
        elif rule.from_group is not None:
            if 0 <= rule.from_group < len(group_hits) and group_hits[rule.from_group]:
                slots[rule.name] = group_hits[rule.from_group]
        elif rule.regex:
            matched = re.search(rule.regex, text)
            if matched is not None:
                slots[rule.name] = matched.group(1) if matched.lastindex else matched.group(0)
    return slots


def match(
    text: str, patterns: Sequence[Pattern]
) -> tuple[str, dict[str, Any], int] | None:
    """Return ``(verb, slots, priority)`` for the highest-priority match."""
    normalized = normalize(text)
    if not normalized:
        return None

    best: tuple[int, int, str, dict[str, Any]] | None = None
    # Tie-break: higher priority, then earlier registration order (lower index).
    for index, pattern in enumerate(patterns):
        # Substring excludes match apps.py (" website", " in brave", …).
        if pattern.exclude and any(ex in normalized for ex in pattern.exclude):
            continue

        if pattern.require and not all(
            _contains_phrase(normalized, phrase) for phrase in pattern.require
        ):
            continue

        group_hits: list[str | None] = []
        if pattern.exact:
            phrases = pattern.any_of[0] if pattern.any_of else ()
            if normalized not in phrases:
                continue
            group_hits = [normalized]
        elif pattern.any_of:
            ok = True
            for group in pattern.any_of:
                hit = _group_hit(normalized, group)
                group_hits.append(hit)
                if hit is None:
                    ok = False
                    break
            if not ok:
                continue
        elif not pattern.require:
            # Empty pattern matches nothing.
            continue

        slots = _extract_slots(normalized, pattern, tuple(group_hits))
        candidate = (pattern.priority, -index, pattern.verb, slots)
        if best is None or candidate[:2] > best[:2]:
            best = candidate

    if best is None:
        return None
    return best[2], best[3], best[0]
