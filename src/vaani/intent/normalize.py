"""Utterance normalization for intent matching."""
from __future__ import annotations


def normalize(text: str) -> str:
    """Casefold, strip, and collapse internal whitespace."""
    return " ".join(text.casefold().strip().split())
