"""Tech and user vocabulary for intent matching (ROADMAP P2-09)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


# Spoken / misheard phrases → canonical tech tokens used in matching.
_BUILTIN: tuple[tuple[str, str], ...] = (
    ("pie test", "pytest"),
    ("py test", "pytest"),
    ("node package manager", "npm"),
    ("p n p m", "pnpm"),
    ("p npm", "pnpm"),
    ("kube control", "kubectl"),
    ("cube control", "kubectl"),
    ("kube ctl", "kubectl"),
    ("engine x", "nginx"),
    ("en gin x", "nginx"),
    ("vs code", "vscode"),
    ("vise code", "vscode"),
    ("v s code", "vscode"),
    ("git hub", "github"),
    ("git hub cli", "gh"),
    ("g h", "gh"),
)


@dataclass(frozen=True)
class Lexicon:
    """Phrase replacements applied during normalization (longest match first)."""

    replacements: tuple[tuple[str, str], ...] = ()

    @classmethod
    def builtin(cls) -> Lexicon:
        """Built-in tech lexicon for common ASR mishears."""
        return cls(_sorted_pairs(_BUILTIN))

    @classmethod
    def load(cls, path: Path | str) -> Lexicon:
        """Load user vocabulary from JSON.

        Accepted shapes:
        - ``{"replacements": {"spoken": "canonical", ...}}``
        - ``{"spoken": "canonical", ...}`` flat map (non-object values ignored)
        - ``["term", ...]`` identity entries (bias presence / future prompt use)
        Missing files yield an empty lexicon.
        """
        file_path = Path(path)
        if not file_path.is_file():
            return cls()
        try:
            raw = json.loads(file_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        pairs: list[tuple[str, str]] = []
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, str) and item.strip():
                    key = " ".join(item.casefold().split())
                    pairs.append((key, key))
        elif isinstance(raw, dict):
            mapping: Mapping[str, object]
            nested = raw.get("replacements")
            if isinstance(nested, dict):
                mapping = nested
            else:
                mapping = raw
            for key, value in mapping.items():
                if not isinstance(key, str) or not isinstance(value, str):
                    continue
                spoken = " ".join(key.casefold().split())
                canonical = " ".join(value.casefold().split())
                if spoken and canonical:
                    pairs.append((spoken, canonical))
        return cls(_sorted_pairs(pairs))

    @classmethod
    def for_matching(cls, vocab_path: Path | str | None = None) -> Lexicon:
        """Builtin tech lexicon merged with optional user ``vocab.json``."""
        base = cls.builtin()
        if vocab_path is None:
            return base
        return base.merged(cls.load(vocab_path))

    def merged(self, other: Lexicon) -> Lexicon:
        """Return a lexicon where ``other`` overrides ``self`` on key clash."""
        by_key = dict(self.replacements)
        by_key.update(dict(other.replacements))
        return Lexicon(_sorted_pairs(by_key.items()))

    def apply(self, text: str) -> str:
        """Replace lexicon phrases in already-casefolded ``text``."""
        if not text or not self.replacements:
            return text
        result = text
        for spoken, canonical in self.replacements:
            if spoken == canonical:
                continue
            pattern = re.compile(rf"(?<!\w){re.escape(spoken)}(?!\w)")
            result = pattern.sub(canonical, result)
        return " ".join(result.split())


def _sorted_pairs(pairs: Mapping[str, str] | list[tuple[str, str]] | tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
    items = list(pairs.items()) if isinstance(pairs, Mapping) else list(pairs)
    # Longest spoken phrase first so "py test" wins over shorter overlaps.
    items.sort(key=lambda item: (-len(item[0]), item[0]))
    return tuple(items)
