"""Helpers for ``agent.task`` seeding and diff-before-apply (plan T7.2).

Keeps CodexRunner as the default backend until T7.1 brains land. Call sites
should prefer a BrainProtocol adapter when one is injected.
"""
from __future__ import annotations

import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from vaani.intent.schema import FocusInfo, PendingAction, RiskClass
from vaani.verbs.packs.project import LastRun

# TODO(T7.1): replace CodexRunner calls with BrainProtocol.run_task when
# brains/{protocol,codex,claude,cursor}.py land on feat/av-brains.

_DIFF_HUNK_RE = re.compile(r"(?m)^(?:--- |\+\+\+ |@@ )")
_FAIL_MARKERS = ("FAILED", "FAILURES", "Error:", "AssertionError", "exit=")
_MAX_SEED_CHARS = 6000
_PENDING_TTL_S = 20.0

PROPOSAL_INSTRUCTION = (
    "Return a unified diff only for the proposed change. "
    "Do not modify files; Vaani will apply after the user confirms."
)


@dataclass(frozen=True)
class AgentSeed:
    """Prompt enrichment for UI-EDIT-05/06."""

    prompt: str
    seeded: bool
    failing_key: str | None = None


def last_failing_run(
    memory: Sequence[LastRun] | None,
    *,
    get_supervisor: Callable[[], Any | None] | None = None,
) -> LastRun | None:
    """Prefer in-memory project LastRun; fall back to Supervisor job logs."""
    if memory:
        for run in reversed(memory):
            if run.returncode != 0 and (run.log_text or "").strip():
                return run
    if get_supervisor is None:
        return None
    supervisor = get_supervisor()
    if supervisor is None:
        return None
    return _failing_from_supervisor(supervisor)


def _failing_from_supervisor(supervisor: Any) -> LastRun | None:
    """Best-effort: find a supervised job whose log looks like a failure."""
    list_jobs = getattr(supervisor, "list_jobs", None)
    logs_fn = getattr(supervisor, "logs", None)
    if not callable(list_jobs) or not callable(logs_fn):
        return None
    try:
        jobs = list(list_jobs())
    except Exception:
        return None
    for job in reversed(jobs):
        key = str(getattr(job, "key", "") or "")
        status = str(getattr(job, "status", "") or "").casefold()
        if status == "running":
            continue
        try:
            log_text = str(logs_fn(key, tail=400) or "")
        except TypeError:
            try:
                log_text = str(logs_fn(key) or "")
            except Exception:
                continue
        except Exception:
            continue
        if not log_text.strip():
            continue
        if not any(marker in log_text for marker in _FAIL_MARKERS):
            # Still allow test-named jobs with any non-empty stopped log.
            if "test" not in key.casefold() and "test" not in str(
                getattr(job, "verb", "")
            ).casefold():
                continue
        return LastRun(
            key=key or "job",
            verb=str(getattr(job, "verb", key) or key),
            argv=tuple(getattr(job, "argv", ()) or ()),
            cwd=str(getattr(job, "cwd", "") or ""),
            returncode=1,
            log_text=log_text,
            started_at=float(getattr(job, "started_at", time.time()) or time.time()),
        )
    return None


def seed_agent_prompt(
    prompt: str,
    *,
    failing: LastRun | None = None,
    focus: FocusInfo | None = None,
    propose_diff: bool = True,
) -> AgentSeed:
    """Build a seeded agent prompt (failing job + focus) for UI-EDIT-05/06."""
    base = (prompt or "").strip()
    parts: list[str] = [base] if base else []
    seeded = False
    failing_key: str | None = None

    if focus is not None:
        doc = focus.document_path
        if doc is not None:
            parts.append(f"Focused file: {doc}")
            seeded = True
        selection = (focus.selection or "").strip()
        if selection:
            clipped = selection if len(selection) <= 2000 else selection[:2000] + "…"
            parts.append(f"Current selection:\n{clipped}")
            seeded = True

    if failing is not None and (failing.log_text or "").strip():
        failing_key = failing.key
        log = failing.log_text.strip()
        if len(log) > _MAX_SEED_CHARS:
            log = log[-_MAX_SEED_CHARS:]
        parts.append(
            "Last failing job "
            f"({failing.verb or failing.key}, exit {failing.returncode}):\n{log}"
        )
        seeded = True

    if propose_diff:
        parts.append(PROPOSAL_INSTRUCTION)

    return AgentSeed(
        prompt="\n\n".join(parts).strip(),
        seeded=seeded,
        failing_key=failing_key,
    )


def extract_unified_diff(text: str) -> str | None:
    """Return a unified-diff excerpt when the agent output looks like a patch."""
    body = (text or "").strip()
    if not body:
        return None
    if not _DIFF_HUNK_RE.search(body):
        return None
    # Prefer from the first diff marker to EOF.
    match = re.search(r"(?m)^(--- |\+\+\+ )", body)
    if match is None:
        match = re.search(r"(?m)^@@ ", body)
    if match is None:
        return None
    diff = body[match.start() :].strip()
    return diff or None


def materialize_agent_argv(
    slots: Mapping[str, Any],
    *,
    executable: str = "codex",
) -> tuple[str, ...]:
    """Dry-run / confirm argv for ``agent.task`` (seeded prompt when present)."""
    from vaani.codex import CodexRunner

    prompt = str(
        slots.get("seeded_prompt")
        or slots.get("proposed_diff")
        or slots.get("prompt")
        or ""
    )
    if slots.get("proposed_diff"):
        return ("agent.task", "apply-diff", prompt[:200])
    return tuple(CodexRunner.command_for(executable, prompt))


def agent_pending(
    *,
    slots: Mapping[str, Any],
    materialized: tuple[str, ...],
    risk: RiskClass = RiskClass.R2,
) -> PendingAction:
    return PendingAction(
        id=f"agent-{int(time.time() * 1000) % 10_000_000:07d}",
        verb="agent.task",
        slots=dict(slots),
        materialized=materialized,
        risk=risk,
        expires_at=time.time() + _PENDING_TTL_S,
    )
