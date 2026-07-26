"""Confirmation engine for R2+ verbs (plan T2.1 / ROADMAP P2-05).

Pending actions are single-use, TTL ~20s, and invalidated by a new utterance.
Approve paths are pill click, Enter, or control-file commands — never voice
"yes" in v1. The brain cannot self-approve R3/R4 (``via="agent"`` rejected).

Dry-run short-circuits in ``policy.dryrun.dispatch``. Undo recording happens
in the controller after a successful mutating dispatch (``UndoStack.record_success``).
"""
from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from vaani.intent.schema import (
    Context,
    Intent,
    PendingAction,
    RiskClass,
    Verb,
)

DEFAULT_TTL_SECONDS = 20.0

_AGENT_BLOCKED = frozenset({RiskClass.R3, RiskClass.R4})
_CONFIRM_RISKS = frozenset({RiskClass.R2, RiskClass.R3, RiskClass.R4})


def requires_confirm(risk: RiskClass) -> bool:
    """True when the verb must stage a PendingAction before the handler runs."""
    return risk in _CONFIRM_RISKS


@dataclass
class _Staged:
    action: PendingAction
    intent: Intent
    verb: Verb
    context: Context


class ConfirmEngine:
    """Process-lifetime pending-action gate.

    ``expire_tick`` is intended for :class:`~vaani.session_loop.SessionLoop`.
    """

    def __init__(
        self,
        *,
        ttl: float = DEFAULT_TTL_SECONDS,
        clock: Callable[[], float] | None = None,
        id_factory: Callable[[], str] | None = None,
        on_change: Callable[[PendingAction | None], None] | None = None,
    ) -> None:
        self._ttl = max(0.1, float(ttl))
        self._clock = clock or time.monotonic
        self._id_factory = id_factory or (lambda: secrets.token_hex(8))
        self._on_change = on_change
        self._lock = threading.RLock()
        self._staged: _Staged | None = None
        self._execution: _Staged | None = None
        self._consumed: set[str] = set()

    def peek(self) -> PendingAction | None:
        with self._lock:
            staged = self._staged
            if staged is None:
                return None
            if self._clock() >= staged.action.expires_at:
                return None
            return staged.action

    def stage(
        self,
        intent: Intent,
        verb: Verb,
        materialized: tuple[str, ...] | Mapping[str, Any] | list[str],
        *,
        context: Context | None = None,
    ) -> PendingAction:
        """Replace any existing pending action and return the new one."""
        if isinstance(materialized, Mapping):
            argv = tuple(str(v) for v in materialized.values())
        else:
            argv = tuple(str(part) for part in materialized)
        if context is None:
            raise TypeError("ConfirmEngine.stage requires context=")
        slots = dict(intent.slots)
        action = PendingAction(
            id=self._id_factory(),
            verb=verb.name,
            slots=slots,
            materialized=argv,
            risk=verb.risk,
            expires_at=self._clock() + self._ttl,
        )
        with self._lock:
            self._staged = _Staged(
                action=action, intent=intent, verb=verb, context=context
            )
            self._execution = None
        self._emit(action)
        return action

    def approve(self, action_id: str, *, via: str) -> PendingAction | None:
        """Consume a pending action once. Returns None when rejected/expired."""
        with self._lock:
            staged = self._staged
            if staged is None:
                return None
            action = staged.action
            if action.id != action_id:
                return None
            if action_id in self._consumed:
                return None
            if self._clock() >= action.expires_at:
                self._staged = None
                self._emit(None)
                return None
            if via == "agent" and action.risk in _AGENT_BLOCKED:
                return None
            self._consumed.add(action_id)
            self._staged = None
            self._execution = staged
            approved = action
        self._emit(None)
        return approved

    def reject(self, action_id: str) -> PendingAction | None:
        """Cancel a pending action by id. Returns the rejected action or None."""
        with self._lock:
            staged = self._staged
            if staged is None or staged.action.id != action_id:
                return None
            rejected = staged.action
            self._staged = None
            self._consumed.add(action_id)
        self._emit(None)
        return rejected

    def invalidate(self) -> PendingAction | None:
        """Cancel any pending action (new utterance / target change). Never approves."""
        with self._lock:
            staged = self._staged
            if staged is None:
                return None
            rejected = staged.action
            self._staged = None
            self._consumed.add(rejected.id)
        self._emit(None)
        return rejected

    def expire_tick(self) -> PendingAction | None:
        """Drop expired pending actions. Returns the expired action, if any."""
        with self._lock:
            staged = self._staged
            if staged is None:
                return None
            if self._clock() < staged.action.expires_at:
                return None
            expired = staged.action
            self._consumed.add(expired.id)
            self._staged = None
        self._emit(None)
        return expired

    def claim_execution(self) -> tuple[Intent, Verb, Context, PendingAction] | None:
        """Return intent/verb/context for the most recently approved action."""
        with self._lock:
            staged = self._execution
            self._execution = None
        if staged is None:
            return None
        return staged.intent, staged.verb, staged.context, staged.action

    def _emit(self, pending: PendingAction | None) -> None:
        if self._on_change is None:
            return
        try:
            self._on_change(pending)
        except Exception:
            pass
