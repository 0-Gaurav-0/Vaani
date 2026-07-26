"""Disambiguation prompts for ambiguous targets (plan T4.5 / spec §12.3).

At most three pill options. Selection is by voice ordinal ("the first one") or
hotkey/control ``select:<id>:<n>``. Timeout and new utterances cancel — never
auto-pick a default.
"""
from __future__ import annotations

import re
import secrets
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vaani.intent.schema import (
    Context,
    DisambiguationOption,
    DisambiguationPrompt,
    Intent,
    Result,
    Status,
    Verb,
)
from vaani.platform.protocol import ProcInfo

MAX_OPTIONS = 3
DEFAULT_TTL_SECONDS = 20.0

# Packs whose R2 handlers self-gate (safe to probe without ``confirmed``).
PROBE_PACKS: frozenset[str] = frozenset({"procs", "project", "core"})

# Verbs probed before ConfirmEngine staging so disambiguation / seeded
# materialize can win first (agent.task seeds failing-job context in-handler).
DISAMBIGUATE_PROBE_VERBS: frozenset[str] = frozenset(
    {"system.proc.kill", "agent.task"}
)

_ORDINAL_WORDS = {
    "first": 0,
    "1st": 0,
    "one": 0,
    "second": 1,
    "2nd": 1,
    "two": 1,
    "third": 2,
    "3rd": 2,
    "three": 2,
}

_ORDINAL_RE = re.compile(
    r"(?i)\b(?:"
    r"(?:the\s+)?(first|second|third|1st|2nd|3rd)(?:\s+one)?"
    r"|"
    r"(?:option|number|choice)\s*(1|2|3|one|two|three)"
    r"|"
    r"^(1|2|3)$"
    r")\b"
)


def truncate_options(
    options: Sequence[DisambiguationOption],
    *,
    limit: int = MAX_OPTIONS,
) -> tuple[DisambiguationOption, ...]:
    """Keep at most ``limit`` options (pill budget)."""
    return tuple(options[: max(0, int(limit))])


def parse_ordinal_choice(text: str) -> int | None:
    """Return a 0-based option index from voice/hotkey text, or None."""
    raw = (text or "").strip()
    if not raw:
        return None
    match = _ORDINAL_RE.search(raw)
    if match is None:
        return None
    token = next(g for g in match.groups() if g)
    key = token.casefold()
    if key in _ORDINAL_WORDS:
        return _ORDINAL_WORDS[key]
    if key.isdigit():
        index = int(key) - 1
        return index if 0 <= index < MAX_OPTIONS else None
    return None


def options_from_procs(
    matches: Sequence[ProcInfo],
) -> tuple[DisambiguationOption, ...]:
    """Build process options; truncated to the pill budget."""
    options = [
        DisambiguationOption(
            key=str(proc.pid),
            label=f"{proc.name} (PID {proc.pid})",
            payload={"pid": proc.pid, "name": proc.name},
        )
        for proc in matches
    ]
    return truncate_options(options)


def options_from_workspaces(
    paths: Sequence[Path | str],
) -> tuple[DisambiguationOption, ...]:
    """Ambiguous workspace candidates (helper for context/editor call sites)."""
    options: list[DisambiguationOption] = []
    for path in paths:
        resolved = Path(path)
        options.append(
            DisambiguationOption(
                key=str(resolved),
                label=resolved.name or str(resolved),
                payload={"workspace": str(resolved)},
            )
        )
    return truncate_options(options)


def options_from_prs(
    prs: Sequence[Mapping[str, Any]],
) -> tuple[DisambiguationOption, ...]:
    """Stub helper for several matching PRs (forge pack wires later)."""
    options: list[DisambiguationOption] = []
    for pr in prs:
        number = pr.get("number")
        title = str(pr.get("title") or f"PR {number}")
        key = str(number if number is not None else pr.get("url") or title)
        options.append(
            DisambiguationOption(
                key=key,
                label=f"#{number} {title}" if number is not None else title,
                payload=dict(pr),
            )
        )
    return truncate_options(options)


def build_prompt(
    *,
    question: str,
    options: Sequence[DisambiguationOption],
    verb: str,
    slots: Mapping[str, Any] | None = None,
    prompt_id: str | None = None,
    expires_at: float | None = None,
    ttl: float = DEFAULT_TTL_SECONDS,
    clock: Callable[[], float] | None = None,
) -> DisambiguationPrompt:
    """Construct a prompt, truncating options to ``MAX_OPTIONS``."""
    now = (clock or time.monotonic)()
    trimmed = truncate_options(options)
    return DisambiguationPrompt(
        id=prompt_id or secrets.token_hex(8),
        question=question,
        options=trimmed,
        verb=verb,
        slots=dict(slots or {}),
        expires_at=expires_at if expires_at is not None else now + max(0.1, float(ttl)),
    )


def disambiguation_result(
    *,
    question: str,
    options: Sequence[DisambiguationOption],
    verb: str,
    slots: Mapping[str, Any] | None = None,
    rung: int = 0,
    prompt_id: str | None = None,
    ttl: float = DEFAULT_TTL_SECONDS,
) -> Result:
    """Build a ``NEEDS_DISAMBIGUATE`` Result with a truncated prompt."""
    prompt = build_prompt(
        question=question,
        options=options,
        verb=verb,
        slots=slots,
        prompt_id=prompt_id,
        ttl=ttl,
        clock=time.time,
    )
    labels = "; ".join(
        f"{index + 1}. {opt.label}" for index, opt in enumerate(prompt.options)
    )
    detail = f"{question} {labels}".strip()
    return Result(
        status=Status.NEEDS_DISAMBIGUATE,
        summary=question[:80],
        detail=detail,
        evidence=tuple(opt.key for opt in prompt.options),
        rung=rung,
        disambiguation=prompt,
    )


def merge_option_slots(
    base: Mapping[str, Any],
    option: DisambiguationOption,
) -> dict[str, Any]:
    """Merge the chosen option payload into verb slots."""
    merged = dict(base)
    merged.update(option.payload)
    return merged


@dataclass
class _Staged:
    prompt: DisambiguationPrompt
    intent: Intent
    verb: Verb
    context: Context


class DisambiguationEngine:
    """Process-lifetime disambiguation gate (mirrors ConfirmEngine semantics)."""

    def __init__(
        self,
        *,
        ttl: float = DEFAULT_TTL_SECONDS,
        clock: Callable[[], float] | None = None,
        id_factory: Callable[[], str] | None = None,
        on_change: Callable[[DisambiguationPrompt | None], None] | None = None,
    ) -> None:
        self._ttl = max(0.1, float(ttl))
        self._clock = clock or time.monotonic
        self._id_factory = id_factory or (lambda: secrets.token_hex(8))
        self._on_change = on_change
        self._lock = threading.RLock()
        self._staged: _Staged | None = None
        self._selection: tuple[_Staged, DisambiguationOption] | None = None
        self._consumed: set[str] = set()

    def peek(self) -> DisambiguationPrompt | None:
        with self._lock:
            staged = self._staged
            if staged is None:
                return None
            if self._clock() >= staged.prompt.expires_at:
                return None
            return staged.prompt

    def stage(
        self,
        intent: Intent,
        verb: Verb,
        prompt: DisambiguationPrompt,
        *,
        context: Context,
    ) -> DisambiguationPrompt:
        """Replace any pending prompt. Options are re-truncated for safety."""
        options = truncate_options(prompt.options)
        action = DisambiguationPrompt(
            id=prompt.id or self._id_factory(),
            question=prompt.question,
            options=options,
            verb=prompt.verb or verb.name,
            slots=dict(prompt.slots or intent.slots),
            expires_at=self._clock() + self._ttl,
        )
        with self._lock:
            self._staged = _Staged(
                prompt=action, intent=intent, verb=verb, context=context
            )
            self._selection = None
        self._emit(action)
        return action

    def select(
        self,
        prompt_id: str,
        index: int,
        *,
        via: str,
    ) -> DisambiguationOption | None:
        """Choose option by 0-based index. Never picks on timeout/miss."""
        del via  # recorded by caller; selection itself is via-agnostic
        with self._lock:
            staged = self._staged
            if staged is None:
                return None
            prompt = staged.prompt
            if prompt.id != prompt_id or prompt_id in self._consumed:
                return None
            if self._clock() >= prompt.expires_at:
                self._staged = None
                self._consumed.add(prompt_id)
                self._emit(None)
                return None
            if index < 0 or index >= len(prompt.options):
                return None
            chosen = prompt.options[index]
            self._consumed.add(prompt_id)
            self._staged = None
            self._selection = (staged, chosen)
        self._emit(None)
        return chosen

    def select_utterance(self, text: str, *, via: str = "voice") -> DisambiguationOption | None:
        """Select from a pending prompt using an ordinal utterance."""
        prompt = self.peek()
        if prompt is None:
            return None
        index = parse_ordinal_choice(text)
        if index is None:
            return None
        return self.select(prompt.id, index, via=via)

    def reject(self, prompt_id: str) -> DisambiguationPrompt | None:
        with self._lock:
            staged = self._staged
            if staged is None or staged.prompt.id != prompt_id:
                return None
            rejected = staged.prompt
            self._staged = None
            self._consumed.add(prompt_id)
        self._emit(None)
        return rejected

    def invalidate(self) -> DisambiguationPrompt | None:
        """Cancel pending prompt (new utterance / Esc). Never selects."""
        with self._lock:
            staged = self._staged
            if staged is None:
                return None
            rejected = staged.prompt
            self._staged = None
            self._consumed.add(rejected.id)
        self._emit(None)
        return rejected

    def expire_tick(self) -> DisambiguationPrompt | None:
        """Drop expired prompts without selecting an option."""
        with self._lock:
            staged = self._staged
            if staged is None:
                return None
            if self._clock() < staged.prompt.expires_at:
                return None
            expired = staged.prompt
            self._consumed.add(expired.id)
            self._staged = None
        self._emit(None)
        return expired

    def claim_selection(
        self,
    ) -> tuple[Intent, Verb, Context, DisambiguationPrompt, DisambiguationOption] | None:
        """Return intent/verb/context plus the chosen option after ``select``."""
        with self._lock:
            bundle = self._selection
            self._selection = None
        if bundle is None:
            return None
        staged, option = bundle
        return staged.intent, staged.verb, staged.context, staged.prompt, option

    def _emit(self, prompt: DisambiguationPrompt | None) -> None:
        if self._on_change is None:
            return
        try:
            self._on_change(prompt)
        except Exception:
            pass
