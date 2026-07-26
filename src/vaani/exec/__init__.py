"""Bounded argv execution primitives (verb runner + process helpers)."""

from .input import TYPED_CAVEAT, FakeInputSynth, UnsupportedInputSynth, with_caveat
from .runner import Command, Completed, powershell, run
from .supervisor import Job, Supervisor

__all__ = [
    "TYPED_CAVEAT",
    "AgentResult",
    "AgentRunner",
    "Command",
    "Completed",
    "FakeInputSynth",
    "Job",
    "Supervisor",
    "UnsupportedInputSynth",
    "parse_agent_wake",
    "powershell",
    "run",
    "with_caveat",
]


def __getattr__(name: str):
    # Lazy: agent imports brains → codex → exec.proc; keep package import acyclic.
    if name in {"AgentResult", "AgentRunner", "parse_agent_wake"}:
        from . import agent as _agent

        return getattr(_agent, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
