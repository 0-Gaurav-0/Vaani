"""Pluggable agent brains (Codex / Claude / Cursor)."""

from .protocol import BrainProtocol, BrainResult, ToolCall, ToolSpec

BRAIN_NAMES: frozenset[str] = frozenset({"codex", "claude", "cursor"})

__all__ = [
    "BRAIN_NAMES",
    "BrainProtocol",
    "BrainResult",
    "ClaudeBrain",
    "CodexBrain",
    "CursorBrain",
    "ToolCall",
    "ToolSpec",
    "default_brains",
]


def default_brains() -> dict[str, BrainProtocol]:
    """Construct the three stock brain adapters."""
    from .claude import ClaudeBrain
    from .codex import CodexBrain
    from .cursor import CursorBrain

    return {
        "codex": CodexBrain(),
        "claude": ClaudeBrain(),
        "cursor": CursorBrain(),
    }


def __getattr__(name: str):
    if name == "CodexBrain":
        from .codex import CodexBrain

        return CodexBrain
    if name == "ClaudeBrain":
        from .claude import ClaudeBrain

        return ClaudeBrain
    if name == "CursorBrain":
        from .cursor import CursorBrain

        return CursorBrain
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
