"""Day-scoped assistant Q&A memory (markdown files)."""
from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

DEFAULT_MEMORY_ROOT = Path.home() / ".local" / "share" / "vaani" / "memory"
MAX_CONTEXT_CHARS = 3000
MAX_TURNS = 8


def memory_root() -> Path:
    override = os.environ.get("VAANI_MEMORY_DIR")
    return Path(override) if override else DEFAULT_MEMORY_ROOT


def day_path(when: datetime | None = None, *, root: Path | None = None) -> Path:
    stamp = (when or datetime.now()).strftime("%Y-%m-%d")
    return (root or memory_root()) / f"{stamp}.md"


def append_turn(
    question: str,
    answer: str,
    *,
    when: datetime | None = None,
    root: Path | None = None,
) -> Path:
    """Append one Q&A turn to today's memory file."""
    path = day_path(when, root=root)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    ts = (when or datetime.now()).strftime("%H:%M")
    q = " ".join((question or "").split())
    a = " ".join((answer or "").split())
    block = f"## {ts}\n**Q:** {q}\n**A:** {a}\n\n"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(block)
    try:
        path.chmod(0o600)
    except Exception:
        pass
    return path


def _parse_turns(text: str) -> list[tuple[str, str]]:
    turns: list[tuple[str, str]] = []
    for match in re.finditer(
        r"##\s*[^\n]*\n\*\*Q:\*\*\s*(.*?)\n\*\*A:\*\*\s*(.*?)(?=\n##\s|\Z)",
        text,
        flags=re.S,
    ):
        q = " ".join(match.group(1).split())
        a = " ".join(match.group(2).split())
        if q or a:
            turns.append((q, a))
    return turns


def load_context(
    *,
    when: datetime | None = None,
    root: Path | None = None,
    max_turns: int = MAX_TURNS,
    max_chars: int = MAX_CONTEXT_CHARS,
) -> str:
    """Return recent today's Q&A as plain context for the model."""
    path = day_path(when, root=root)
    if not path.is_file():
        return ""
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception:
        return ""
    turns = _parse_turns(raw)
    if not turns:
        return ""
    selected = turns[-max(1, max_turns) :]
    lines: list[str] = []
    for q, a in selected:
        lines.append(f"Q: {q}")
        lines.append(f"A: {a}")
        lines.append("")
    context = "\n".join(lines).strip()
    if len(context) <= max_chars:
        return context
    # Keep the newest portion within the budget.
    return context[-max_chars:].lstrip()
