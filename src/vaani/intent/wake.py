"""Wake-phrase detection for rung-6 agent opt-in (spec §12.5)."""
from __future__ import annotations

import re


_WAKE_AGENT_RE = re.compile(
    r"^\s*(?:vaani\s*[,:]?\s*)?agent\s*:\s*(.+)$",
    flags=re.IGNORECASE | re.DOTALL,
)
_BRAIN_FOR_THIS_RE = re.compile(
    r"^\s*use\s+(claude|codex|cursor)\s+for\s+this\b\s*[:\-]?\s*(.*)$",
    flags=re.IGNORECASE | re.DOTALL,
)


def parse_agent_wake(transcript: str) -> tuple[str, str | None] | None:
    """Detect wake / brain-select phrases that force rung 6.

    Returns ``(prompt, brain)`` when matched, else ``None``.

    * ``Vaani, agent: <task>`` — forces ``agent.task``, default brain
    * ``use Claude|Codex|Cursor for this[: ] <task>`` — selects brain
    * Combined forms are supported (wake first, then brain select).
    """
    raw = transcript.strip()
    if not raw:
        return None
    brain: str | None = None
    prompt = raw

    wake = _WAKE_AGENT_RE.match(prompt)
    if wake:
        prompt = wake.group(1).strip()

    brain_match = _BRAIN_FOR_THIS_RE.match(prompt)
    if brain_match:
        brain = brain_match.group(1).lower()
        prompt = (brain_match.group(2) or "").strip()
    elif wake is None:
        return None

    return prompt, brain
