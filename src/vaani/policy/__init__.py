"""Policy layer: dry-run, confirm, undo, risk (shared, OS-agnostic)."""
from __future__ import annotations

from vaani.policy.confirm import ConfirmEngine, requires_confirm
from vaani.policy.dryrun import dispatch, materialize_argv
from vaani.policy.undo import UndoClass, UndoStack, classify_verb, irreversible_refusal

__all__ = [
    "ConfirmEngine",
    "UndoClass",
    "UndoStack",
    "classify_verb",
    "dispatch",
    "irreversible_refusal",
    "materialize_argv",
    "requires_confirm",
]
