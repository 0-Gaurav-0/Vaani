"""Installable verb packs. ``core``/``procs``/``project``/``window`` are always on."""

from vaani.verbs.packs.docker import DOCKER_VERB_NAMES
from vaani.verbs.packs.forge import FORGE_VERB_NAMES
from vaani.verbs.packs.pkg import PKG_VERB_NAMES
from vaani.verbs.packs.procs import PROCS_VERB_NAMES
from vaani.verbs.packs.project import PROJECT_VERB_NAMES
from vaani.verbs.packs.registry import (
    ALWAYS_ON_PACKS,
    BUILTIN_PACKS,
    INSTALLABLE_PACKS,
    PackDescriptor,
    PackError,
    PackPrerequisiteError,
    PackRegistry,
    missing_binary_reason,
    register_stub_packs,
)
from vaani.verbs.packs.window import WINDOW_VERB_NAMES

__all__ = [
    "ALWAYS_ON_PACKS",
    "BUILTIN_PACKS",
    "DOCKER_VERB_NAMES",
    "FORGE_VERB_NAMES",
    "INSTALLABLE_PACKS",
    "PKG_VERB_NAMES",
    "PROCS_VERB_NAMES",
    "PROJECT_VERB_NAMES",
    "WINDOW_VERB_NAMES",
    "PackDescriptor",
    "PackError",
    "PackPrerequisiteError",
    "PackRegistry",
    "missing_binary_reason",
    "register_stub_packs",
]
