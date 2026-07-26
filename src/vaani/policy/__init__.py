"""Policy layer: dry-run, confirm, disambiguate, undo, risk (shared, OS-agnostic)."""
from __future__ import annotations

from vaani.policy.confirm import ConfirmEngine, requires_confirm
from vaani.policy.disambiguate import (
    DisambiguationEngine,
    MAX_OPTIONS,
    disambiguation_result,
    parse_ordinal_choice,
    truncate_options,
)
from vaani.policy.dryrun import attach_workspace, dispatch, materialize_argv
from vaani.policy.undo import UndoClass, UndoStack, classify_verb, irreversible_refusal

__all__ = [
    "ConfirmEngine",
    "DisambiguationEngine",
    "MAX_OPTIONS",
    "UndoClass",
    "UndoStack",
    "attach_workspace",
    "classify_verb",
    "disambiguation_result",
    "dispatch",
    "irreversible_refusal",
    "materialize_argv",
    "parse_ordinal_choice",
    "requires_confirm",
    "truncate_options",
]
