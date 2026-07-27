"""Core intent-stack contracts (spec §5).

This module is the shared spine imported by nearly every layer. It may import
``PlatformId`` from ``vaani.platform.protocol`` only — no other ``vaani`` imports.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping

from vaani.platform.protocol import PlatformId

_SUMMARY_MAX = 80


class Status(str, Enum):
    OK = "ok"
    DRY_RUN = "dry_run"
    NEEDS_CONFIRM = "needs_confirm"
    NEEDS_DISAMBIGUATE = "needs_disambiguate"
    REFUSED = "refused"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"
    PARTIAL = "partial"


class RiskClass(str, Enum):
    """Verb risk policy (§5.5). Risk is a property of the verb, not phrasing."""

    R0 = "R0"  # silent — run immediately, reversible
    R1 = "R1"  # notify — run immediately, report what changed
    R2 = "R2"  # confirm — one confirmation
    R3 = "R3"  # high friction — confirm + materialized argv; never brain-auto
    R4 = "R4"  # blocked by default — config opt-in, then behaves as R3


class Support(str, Enum):
    SUPPORTED = "supported"
    DEGRADED = "degraded"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class SlotSpec:
    """Catalog slot declaration for a verb."""

    type: str
    required: bool = True
    default: Any = None


@dataclass(frozen=True)
class RepoInfo:
    root: Path
    branch: str | None = None
    dirty: bool = False
    upstream: str | None = None
    remote_host: str | None = None


@dataclass(frozen=True)
class ProjectProfile:
    """Detected project tooling (§12.8). Command fields are argv tuples."""

    root: Path
    manager: str | None = None
    test: tuple[str, ...] | None = None
    build: tuple[str, ...] | None = None
    dev: tuple[str, ...] | None = None
    typecheck: tuple[str, ...] | None = None
    lint: tuple[str, ...] | None = None
    format: tuple[str, ...] | None = None
    compose_file: Path | None = None
    env_files: tuple[Path, ...] = ()


@dataclass(frozen=True)
class FocusInfo:
    app_id: str | None = None
    window_title: str | None = None
    document_path: Path | None = None
    selection: str | None = None


@dataclass(frozen=True)
class ScreenFrame:
    """In-memory capture only — never written to disk (§11.1 privacy)."""

    width: int
    height: int
    data: bytes | None = None
    display_index: int = 0


@dataclass(frozen=True)
class AgentSession:
    id: str
    brain: str
    started_at: float
    last_active_at: float


@dataclass(frozen=True)
class Intent:
    verb: str
    slots: Mapping[str, Any]
    rung: int
    confidence: float
    source: str
    mode: str
    utterance: str
    raw_utterance: str
    modifiers: frozenset[str]
    brain: str | None


@dataclass(frozen=True)
class PlanStep:
    verb: str
    slots: Mapping[str, Any]
    note: str | None = None


@dataclass(frozen=True)
class IntentPlan:
    """Ordered executable plan from the parse LLM (or a single grammar hit wrapped)."""

    steps: tuple[PlanStep, ...]
    utterance: str
    raw_utterance: str
    source: str  # "llm" | "grammar"
    confidence: float
    delegate_prompt: str | None = None
    refuse_reason: str | None = None


@dataclass(frozen=True)
class Context:
    platform: PlatformId
    workspace: Path | None
    workspace_source: str
    repo: RepoInfo | None
    project: ProjectProfile | None
    focus: FocusInfo | None
    screen: ScreenFrame | None
    session: AgentSession | None


@dataclass(frozen=True)
class PendingAction:
    id: str
    verb: str
    slots: Mapping[str, Any]
    materialized: tuple[str, ...]
    risk: RiskClass
    expires_at: float


@dataclass(frozen=True)
class DisambiguationOption:
    """One selectable target in a disambiguation prompt (spec §12.3)."""

    key: str
    label: str
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class DisambiguationPrompt:
    """Pill-facing choice of at most 3 options; timeout cancels, never defaults."""

    id: str
    question: str
    options: tuple[DisambiguationOption, ...]
    verb: str
    slots: Mapping[str, Any]
    expires_at: float


@dataclass(frozen=True)
class UndoToken:
    verb: str
    inverse_verb: str
    slots: Mapping[str, Any]
    expires_at: float


@dataclass(frozen=True)
class OverlayOp:
    """Guide overlay op: point / caption / tour (OpenClicky-shaped)."""

    kind: str
    x: float = 0.0
    y: float = 0.0
    label: str = ""
    text: str = ""
    steps: tuple[OverlayOp, ...] = ()


@dataclass(frozen=True)
class Result:
    status: Status
    summary: str
    detail: str = ""
    evidence: tuple[str, ...] = ()
    rung: int = 0
    undo: UndoToken | None = None
    pending: PendingAction | None = None
    disambiguation: DisambiguationPrompt | None = None
    overlay: tuple[OverlayOp, ...] = ()
    # Invariant 7: every result names its workspace source (§5.2 / §13.5.7).
    workspace: Path | None = None
    workspace_source: str = ""

    def __post_init__(self) -> None:
        if len(self.summary) > _SUMMARY_MAX:
            object.__setattr__(self, "summary", self.summary[:_SUMMARY_MAX])


@dataclass(frozen=True)
class Verb:
    name: str
    title: str
    slots: Mapping[str, SlotSpec]
    rung: int
    risk: RiskClass
    requires: frozenset[str]
    support: Mapping[PlatformId, Support]
    undo: str | None
    pack: str
    handler: Callable[[Intent, Context], Result]
