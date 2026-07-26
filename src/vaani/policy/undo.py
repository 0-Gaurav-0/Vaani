"""Bounded undo stack (plan T2.3 / spec §12.10).

Three classes:
- **reversible** — toggle/inverse slots (volume, DND, Wi-Fi, …)
- **compensatable** — paired verbs (stash↔pop, branch create↔delete, …)
- **irreversible** — recorded so ``session.undo`` can refuse plainly
"""
from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from vaani.intent.grammar import Pattern
from vaani.intent.schema import (
    Context,
    Intent,
    Result,
    RiskClass,
    SlotSpec,
    Status,
    Support,
    UndoToken,
    Verb,
)
from vaani.platform.protocol import PlatformId
from vaani.verbs.registry import Registry

DEFAULT_TTL_SECONDS = 60.0
MAX_STACK = 10

UNDO_VERB_NAMES: frozenset[str] = frozenset({"session.undo"})

_ALL_SUPPORT = {
    PlatformId.LINUX: Support.SUPPORTED,
    PlatformId.MACOS: Support.SUPPORTED,
    PlatformId.WINDOWS: Support.SUPPORTED,
}


class UndoClass(str, Enum):
    REVERSIBLE = "reversible"
    COMPENSATABLE = "compensatable"
    IRREVERSIBLE = "irreversible"


# Verb → class. Compensatable pairs also appear in COMPENSATABLE_INVERSE.
IRREVERSIBLE_VERBS: frozenset[str] = frozenset(
    {
        "system.proc.kill",
        "system.port.free",
        "system.trash.empty",
    }
)

REVERSIBLE_VERBS: frozenset[str] = frozenset(
    {
        "system.volume.set",
        "system.dnd.set",
        "system.wifi.set",
    }
)

# Forward verb → inverse verb (compensatable pairs from the plan/spec).
COMPENSATABLE_INVERSE: dict[str, str] = {
    "vcs.stash.push": "vcs.stash.pop",
    "vcs.stash.pop": "vcs.stash.push",
    "vcs.branch.create": "vcs.branch.delete",
    "vcs.branch.delete": "vcs.branch.create",
    "files.mkdir": "files.rmdir",
    "files.rmdir": "files.mkdir",
    "files.sweep": "files.sweep",
}

_IRREVERSIBLE_REFUSALS: dict[str, str] = {
    "system.proc.kill": "killing a process can't be undone",
    "system.port.free": "killing a process can't be undone",
    "system.trash.empty": "emptying the trash can't be undone",
}


@dataclass(frozen=True)
class UndoEntry:
    token: UndoToken
    undo_class: UndoClass


def classify_verb(verb_name: str) -> UndoClass | None:
    """Return the undo class for a verb, or None when undo is not tracked."""
    if verb_name in IRREVERSIBLE_VERBS:
        return UndoClass.IRREVERSIBLE
    if verb_name in REVERSIBLE_VERBS:
        return UndoClass.REVERSIBLE
    if verb_name in COMPENSATABLE_INVERSE:
        return UndoClass.COMPENSATABLE
    return None


def irreversible_refusal(verb_name: str) -> str:
    """Plain refusal text for an irreversible action (never a partial attempt)."""
    return _IRREVERSIBLE_REFUSALS.get(verb_name, f"{verb_name} can't be undone")


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).casefold() in {"1", "true", "yes", "on"}


def inverse_slots(verb_name: str, slots: Mapping[str, Any]) -> dict[str, Any] | None:
    """Build slots for the inverse action, or None when no inverse is known."""
    if verb_name == "system.dnd.set":
        if "enabled" not in slots:
            return None
        return {"enabled": not _truthy(slots.get("enabled"))}

    if verb_name == "system.wifi.set":
        if "enabled" not in slots:
            return None
        return {"enabled": not _truthy(slots.get("enabled"))}

    if verb_name == "system.volume.set":
        if "muted" in slots:
            return {"muted": not _truthy(slots.get("muted"))}
        if "previous_level" in slots:
            try:
                return {"level": int(slots["previous_level"])}
            except (TypeError, ValueError):
                return None
        # Level without a captured previous value is not safely invertible.
        return None

    if verb_name == "files.sweep":
        # Inverse move: swap source/dest when both are present.
        src = slots.get("dest") or slots.get("to")
        dest = slots.get("source") or slots.get("from")
        if src is None or dest is None:
            return None
        out = dict(slots)
        out["source"] = src
        out["dest"] = dest
        out.pop("from", None)
        out.pop("to", None)
        return out

    if verb_name in COMPENSATABLE_INVERSE:
        return dict(slots)

    return None


def build_inverse_token(
    verb_name: str,
    slots: Mapping[str, Any],
    *,
    inverse_verb: str | None = None,
    clock: Callable[[], float] | None = None,
    ttl: float = DEFAULT_TTL_SECONDS,
) -> UndoToken | None:
    """Construct an UndoToken for a successful mutating verb, if invertible."""
    now = (clock or time.time)()
    classification = classify_verb(verb_name)
    if classification is UndoClass.IRREVERSIBLE:
        return UndoToken(
            verb=verb_name,
            inverse_verb="",
            slots=dict(slots),
            expires_at=now + ttl,
        )

    inv_verb = inverse_verb
    if inv_verb is None:
        if classification is UndoClass.COMPENSATABLE:
            inv_verb = COMPENSATABLE_INVERSE.get(verb_name)
        elif classification is UndoClass.REVERSIBLE:
            inv_verb = verb_name
    if not inv_verb:
        return None

    inv_slots = inverse_slots(verb_name, slots)
    if inv_slots is None:
        return None
    return UndoToken(
        verb=verb_name,
        inverse_verb=inv_verb,
        slots=inv_slots,
        expires_at=now + ttl,
    )


class UndoStack:
    """Process-lifetime LIFO of undo entries (max ``MAX_STACK``)."""

    def __init__(
        self,
        *,
        max_size: int = MAX_STACK,
        clock: Callable[[], float] | None = None,
        ttl: float = DEFAULT_TTL_SECONDS,
    ) -> None:
        self._max_size = max(1, int(max_size))
        self._clock = clock or time.time
        self._ttl = max(0.1, float(ttl))
        self._lock = threading.RLock()
        self._entries: deque[UndoEntry] = deque()

    def __len__(self) -> int:
        with self._lock:
            self._drop_expired()
            return len(self._entries)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def push_inverse(
        self,
        token: UndoToken,
        *,
        undo_class: UndoClass | None = None,
    ) -> None:
        """Push a known inverse. Oldest entries drop when the stack is full."""
        classification = undo_class or classify_verb(token.verb) or UndoClass.REVERSIBLE
        entry = UndoEntry(token=token, undo_class=classification)
        with self._lock:
            self._drop_expired()
            self._entries.append(entry)
            while len(self._entries) > self._max_size:
                self._entries.popleft()

    def peek(self) -> UndoEntry | None:
        with self._lock:
            self._drop_expired()
            if not self._entries:
                return None
            return self._entries[-1]

    def pop(self) -> UndoEntry | None:
        """Pop the newest non-expired entry, or None when empty."""
        with self._lock:
            self._drop_expired()
            if not self._entries:
                return None
            return self._entries.pop()

    def record_success(
        self,
        verb: Verb,
        intent: Intent,
        result: Result,
    ) -> None:
        """Hook after a successful mutating verb — push when an inverse is known."""
        if result.status is not Status.OK:
            return
        if verb.name == "session.undo":
            return

        classification = classify_verb(verb.name)
        if classification is None and result.undo is None and not verb.undo:
            return

        token = result.undo
        if token is None:
            token = build_inverse_token(
                verb.name,
                intent.slots,
                inverse_verb=verb.undo,
                clock=self._clock,
                ttl=self._ttl,
            )
        if token is None:
            return

        if classification is None:
            classification = classify_verb(verb.name) or UndoClass.REVERSIBLE
        self.push_inverse(token, undo_class=classification)

    def _drop_expired(self) -> None:
        now = self._clock()
        while self._entries and self._entries[-1].token.expires_at <= now:
            self._entries.pop()
        # Also purge expired entries deeper in the stack.
        if not self._entries:
            return
        kept = [e for e in self._entries if e.token.expires_at > now]
        if len(kept) != len(self._entries):
            self._entries = deque(kept)


def undo_patterns() -> tuple[Pattern, ...]:
    """Grammar for ``session.undo``."""
    return (
        Pattern(
            verb="session.undo",
            any_of=(
                (
                    "undo that",
                    "undo",
                    "undo the last action",
                    "undo last action",
                    "reverse that",
                ),
            ),
            exact=True,
            priority=55,
        ),
    )


def build_session_undo_verb(
    stack: UndoStack,
    registry: Registry,
    *,
    dispatch_fn: Callable[[Verb, Intent, Context], Result] | None = None,
) -> Verb:
    """Build the ``session.undo`` verb closed over ``stack`` + ``registry``."""

    def handle_undo(intent: Intent, context: Context) -> Result:
        # Dry-run is short-circuited by policy.dispatch before this runs.
        entry = stack.pop()
        if entry is None:
            return Result(
                status=Status.FAILED,
                summary="Nothing to undo",
                detail="Undo stack is empty or all entries expired",
                rung=0,
            )

        if entry.undo_class is UndoClass.IRREVERSIBLE:
            message = irreversible_refusal(entry.token.verb)
            return Result(
                status=Status.REFUSED,
                summary=message,
                detail=message,
                evidence=(entry.token.verb,),
                rung=0,
            )

        inverse_name = entry.token.inverse_verb
        if not inverse_name:
            message = irreversible_refusal(entry.token.verb)
            return Result(
                status=Status.REFUSED,
                summary=message,
                detail=message,
                evidence=(entry.token.verb,),
                rung=0,
            )

        inverse = registry.get(inverse_name)
        if inverse is None:
            return Result(
                status=Status.FAILED,
                summary=f"No inverse verb {inverse_name}",
                detail="Inverse verb is not registered",
                evidence=(inverse_name,),
                rung=0,
            )

        inverse_intent = Intent(
            verb=inverse_name,
            slots=dict(entry.token.slots),
            rung=inverse.rung,
            confidence=1.0,
            source=intent.source,
            mode=intent.mode,
            utterance=intent.utterance,
            raw_utterance=intent.raw_utterance,
            modifiers=frozenset(set(intent.modifiers) | {"confirmed"}),
            brain=intent.brain,
        )
        run = dispatch_fn
        if run is None:
            from vaani.policy.dryrun import dispatch as run
        result = run(inverse, inverse_intent, context)
        # Record the inverse so a second "undo" can redo toggles.
        if result.status is Status.OK:
            stack.record_success(inverse, inverse_intent, result)
        return Result(
            status=result.status,
            summary=result.summary if result.status is Status.OK else result.summary,
            detail=result.detail or f"Undid {entry.token.verb}",
            evidence=result.evidence or (inverse_name,),
            rung=result.rung,
            undo=result.undo,
        )

    return Verb(
        name="session.undo",
        title="Undo the last action",
        slots={},
        rung=0,
        risk=RiskClass.R0,
        requires=frozenset(),
        support=_ALL_SUPPORT,
        undo=None,
        pack="session",
        handler=handle_undo,
    )


def register_undo(
    registry: Registry,
    stack: UndoStack,
    *,
    dispatch_fn: Callable[[Verb, Intent, Context], Result] | None = None,
) -> tuple[Pattern, ...]:
    """Register ``session.undo`` and return its grammar patterns."""
    registry.register(
        build_session_undo_verb(stack, registry, dispatch_fn=dispatch_fn)
    )
    return undo_patterns()
