"""Installable verb packs. ``core`` is always on; ``procs``/``project`` ship in-tree."""

from vaani.verbs.packs.procs import PROCS_VERB_NAMES
from vaani.verbs.packs.project import PROJECT_VERB_NAMES

__all__ = ["PROCS_VERB_NAMES", "PROJECT_VERB_NAMES"]
