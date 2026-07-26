"""Bounded argv execution primitives (verb runner + process helpers)."""

from .input import TYPED_CAVEAT, FakeInputSynth, UnsupportedInputSynth, with_caveat
from .runner import Command, Completed, powershell, run
from .supervisor import Job, Supervisor

__all__ = [
    "TYPED_CAVEAT",
    "Command",
    "Completed",
    "FakeInputSynth",
    "Job",
    "Supervisor",
    "UnsupportedInputSynth",
    "powershell",
    "run",
    "with_caveat",
]
