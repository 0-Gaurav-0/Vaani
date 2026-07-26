"""Ladder router: grammar first, never an LLM for rung 1/2."""
from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from vaani.intent.grammar import Pattern, match
from vaani.intent.interrogative import (
    is_interrogative,
    port_query_slots,
    strip_interrogative_lead,
)
from vaani.intent.lexicon import Lexicon
from vaani.intent.normalize import detect_modifiers, normalize
from vaani.intent.schema import Context, Intent
from vaani.platform.protocol import PlatformId
from vaani.verbs.registry import Registry


def prefer_verifiable_format(
    intent: Intent,
    context: Context,
    registry: Registry,
    *,
    platform: PlatformId,
) -> Intent:
    """Prefer ``project.format`` (rung 4) over ``editor.format`` (rung 7) (T5.3).

    When a profile formatter exists, keep/upgrade to ``project.format``.
    Otherwise fall back to the keystroke macro when the computer-use pack is on.
    """
    if intent.verb not in {"project.format", "editor.format"}:
        return intent
    has_formatter = bool(
        context.project is not None
        and (context.project.format or context.project.lint)
    )
    enabled = {verb.name for verb in registry.enabled(platform)}

    def _as(verb_name: str) -> Intent:
        verb = registry.get(verb_name)
        rung = verb.rung if verb is not None else (4 if verb_name == "project.format" else 7)
        return Intent(
            verb=verb_name,
            slots=dict(intent.slots),
            rung=rung,
            confidence=intent.confidence,
            source=intent.source,
            mode=intent.mode,
            utterance=intent.utterance,
            raw_utterance=intent.raw_utterance,
            modifiers=intent.modifiers,
            brain=intent.brain,
        )

    if has_formatter and "project.format" in enabled:
        return intent if intent.verb == "project.format" else _as("project.format")
    if "editor.format" in enabled:
        return intent if intent.verb == "editor.format" else _as("editor.format")
    return intent


class Router:
    """Route a transcript to an Intent using the escalation ladder (spec §4)."""

    def __init__(
        self,
        registry: Registry,
        patterns: Sequence[Pattern],
        *,
        resolve_app: Callable[[str], Any] | None = None,
        resolve_site: Callable[[str], Any] | None = None,
        llm_parse: Callable[[str], Intent | None] | None = None,
        lexicon: Lexicon | None = None,
        vocab_path: Path | str | None = None,
    ) -> None:
        self.registry = registry
        self.patterns = tuple(patterns)
        self.resolve_app = resolve_app
        self.resolve_site = resolve_site
        # Present for dependency injection in tests; rung 1/2 must not need it.
        self.llm_parse = llm_parse
        if lexicon is not None:
            self.lexicon = lexicon
        else:
            self.lexicon = Lexicon.for_matching(vocab_path)

    def route(self, transcript: str, *, platform: PlatformId) -> Intent | None:
        """Map ``transcript`` to an Intent for ``platform``, or None."""
        raw = transcript
        modifiers = set(detect_modifiers(transcript))
        interrogative = is_interrogative(raw)
        if interrogative:
            modifiers.add("interrogative")
        # Matching copy: strip leading interrogative so exact patterns still hit
        # ("how do I empty the trash" → "empty the trash").
        match_source = strip_interrogative_lead(raw) if interrogative else raw
        utterance = normalize(match_source, lexicon=self.lexicon)
        full_norm = normalize(raw, lexicon=self.lexicon)
        enabled = {verb.name for verb in self.registry.enabled(platform)}
        mods = frozenset(modifiers)

        # High-priority grammar (≥50) wins before app/site resolvers and before
        # interrogative stripping — so "open this in Cursor" / "what's running"
        # hit project/editor/job verbs instead of app.open or agent.task.
        early = match(full_norm, self.patterns, lexicon=self.lexicon)
        if early is not None:
            verb_name, slots, priority = early
            if priority >= 50 and verb_name in enabled:
                verb = self.registry.get(verb_name)
                rung = verb.rung if verb is not None else 1
                return self._intent(
                    verb_name,
                    dict(slots),
                    rung=rung,
                    utterance=full_norm,
                    raw=raw,
                    modifiers=mods,
                )

        # Rung 1 — today's resolver order (app before site) preserves §4.2.
        if "app.open" in enabled and self.resolve_app is not None:
            app = self.resolve_app(utterance or raw)
            if app is not None:
                name = getattr(app, "name", None) or str(app)
                return self._intent(
                    "app.open",
                    {"name": name, "target": app},
                    rung=1,
                    utterance=utterance,
                    raw=raw,
                    modifiers=mods,
                )

        if "site.open" in enabled and self.resolve_site is not None:
            site = self.resolve_site(utterance or raw)
            if site is not None:
                return self._intent(
                    "site.open",
                    {
                        "name": site.name,
                        "url": site.url,
                        "browser": site.browser,
                    },
                    rung=1,
                    utterance=utterance,
                    raw=raw,
                    modifiers=mods,
                )

        hit = match(utterance, self.patterns, lexicon=self.lexicon)
        if hit is None and interrogative:
            # "what's on port 3000" → port.free after lead strip ("on port 3000").
            port_slots = port_query_slots(raw)
            if port_slots is not None and "system.port.free" in enabled:
                verb = self.registry.get("system.port.free")
                rung = verb.rung if verb is not None else 2
                return self._intent(
                    "system.port.free",
                    port_slots,
                    rung=rung,
                    utterance=utterance,
                    raw=raw,
                    modifiers=mods,
                )
        if hit is not None:
            verb_name, slots, _priority = hit
            if verb_name in enabled:
                verb = self.registry.get(verb_name)
                rung = verb.rung if verb is not None else 1
                # Grammar hits are rung 1/2 — never call the LLM parser.
                return self._intent(
                    verb_name,
                    dict(slots),
                    rung=rung,
                    utterance=utterance,
                    raw=raw,
                    modifiers=mods,
                )

        if "agent.task" in enabled:
            return self._intent(
                "agent.task",
                {"prompt": raw},
                rung=6,
                utterance=utterance,
                raw=raw,
                confidence=0.5,
                source="fallback",
                modifiers=mods,
            )
        return None

    @staticmethod
    def _intent(
        verb: str,
        slots: dict[str, Any],
        *,
        rung: int,
        utterance: str,
        raw: str,
        confidence: float = 1.0,
        source: str = "grammar",
        modifiers: frozenset[str] = frozenset(),
    ) -> Intent:
        return Intent(
            verb=verb,
            slots=slots,
            rung=rung,
            confidence=confidence,
            source=source,
            mode="act",
            utterance=utterance,
            raw_utterance=raw,
            modifiers=modifiers,
            brain=None,
        )
