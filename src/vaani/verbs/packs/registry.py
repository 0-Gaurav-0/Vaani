"""Capability pack registry: descriptors, enable/disable, packs.json I/O.

``core`` / ``procs`` / ``project`` are always on. Installable packs are
individually toggleable and declare binaries, auth, and permissions so a missing
tool surfaces as ``DEGRADED`` ("gh isn't installed") rather than rung-6
escalation (invariant 5).
"""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping

from vaani.verbs.registry import Registry

WhichFn = Callable[[str], str | None]


class PackError(ValueError):
    """Invalid pack operation (unknown name, cannot disable always-on, …)."""


class PackPrerequisiteError(RuntimeError):
    """A declared pack binary is missing from PATH."""

    def __init__(self, binary: str) -> None:
        self.binary = binary
        super().__init__(f"{binary} isn't installed")


@dataclass(frozen=True)
class PackDescriptor:
    """Static declaration for one capability pack."""

    name: str
    title: str
    binaries: tuple[str, ...] = ()
    auth: tuple[str, ...] = ()
    permissions: frozenset[str] = field(default_factory=frozenset)
    always_on: bool = False
    default_enabled: bool = False


# Always-on in-tree packs (cannot be disabled).
ALWAYS_ON_PACKS: tuple[str, ...] = ("core", "procs", "project")

# Installable / individually toggleable packs (T4.1+).
INSTALLABLE_PACKS: tuple[str, ...] = (
    "git",
    "pkg",
    "docker",
    "forge",
    "iac",
    "browser-cdp",
    "guide",
    "computer-use",
)


def _descriptor(
    name: str,
    title: str,
    *,
    binaries: tuple[str, ...] = (),
    auth: tuple[str, ...] = (),
    permissions: frozenset[str] = frozenset(),
    always_on: bool = False,
    default_enabled: bool = False,
) -> PackDescriptor:
    return PackDescriptor(
        name=name,
        title=title,
        binaries=binaries,
        auth=auth,
        permissions=permissions,
        always_on=always_on,
        default_enabled=default_enabled,
    )


BUILTIN_PACKS: dict[str, PackDescriptor] = {
    "core": _descriptor("core", "Apps, sites, browser, system, files", always_on=True),
    "procs": _descriptor("procs", "Ports, processes, trash, wifi", always_on=True),
    "project": _descriptor(
        "project", "Project commands, jobs, terminal/editor", always_on=True
    ),
    "git": _descriptor("git", "Git / VCS verbs", binaries=("git",)),
    "pkg": _descriptor("pkg", "Package manager verbs", binaries=()),
    "docker": _descriptor("docker", "Container verbs", binaries=("docker",)),
    "forge": _descriptor(
        "forge", "Forge / PR verbs (gh)", binaries=("gh",), auth=("gh",)
    ),
    "iac": _descriptor("iac", "Infrastructure-as-code verbs", binaries=()),
    "browser-cdp": _descriptor(
        "browser-cdp",
        "Browser automation via CDP",
        binaries=(),
        permissions=frozenset({"browser"}),
    ),
    "guide": _descriptor(
        "guide",
        "Guide / point-on-screen mode",
        binaries=(),
        permissions=frozenset({"screen"}),
    ),
    "computer-use": _descriptor(
        "computer-use",
        "Synthetic input / computer-use (rung 7)",
        binaries=(),
        permissions=frozenset({"accessibility", "input"}),
        default_enabled=False,
    ),
}


def missing_binary_reason(binary: str) -> str:
    """Canonical DEGRADED note for a missing tool (never a rung-6 prompt)."""
    return f"{binary} isn't installed"


class PackRegistry:
    """Load/save ``packs.json`` and answer enablement / binary questions."""

    def __init__(
        self,
        path: Path | str,
        *,
        descriptors: Mapping[str, PackDescriptor] | None = None,
        which: WhichFn | None = None,
    ) -> None:
        self._path = Path(path)
        self._descriptors = dict(descriptors or BUILTIN_PACKS)
        self._which: WhichFn = which or shutil.which
        # name → enabled override for installable packs only
        self._overrides: dict[str, bool] = {}
        self.load()

    @property
    def path(self) -> Path:
        return self._path

    def descriptors(self) -> tuple[PackDescriptor, ...]:
        order = list(ALWAYS_ON_PACKS) + list(INSTALLABLE_PACKS)
        return tuple(
            self._descriptors[name] for name in order if name in self._descriptors
        )

    def get(self, name: str) -> PackDescriptor | None:
        return self._descriptors.get(name)

    def is_enabled(self, name: str) -> bool:
        desc = self._descriptors.get(name)
        if desc is None:
            return False
        if desc.always_on:
            return True
        if name in self._overrides:
            return self._overrides[name]
        return desc.default_enabled

    def set_enabled(self, name: str, enabled: bool) -> None:
        desc = self._descriptors.get(name)
        if desc is None:
            raise PackError(f"unknown pack: {name}")
        if desc.always_on:
            raise PackError(f"pack {name!r} is always on and cannot be disabled")
        self._overrides[name] = bool(enabled)
        self.save()

    def require_binary(self, binary: str) -> str:
        """Return resolved PATH entry for ``binary``, or raise ``PackPrerequisiteError``."""
        found = self._which(binary)
        if not found:
            raise PackPrerequisiteError(binary)
        return found

    def missing_binaries(self, name: str) -> tuple[str, ...]:
        desc = self._descriptors.get(name)
        if desc is None:
            return ()
        missing: list[str] = []
        for binary in desc.binaries:
            if self._which(binary) is None:
                missing.append(binary)
        return tuple(missing)

    def degraded_reason(self, name: str) -> str | None:
        """If the pack is enabled but a binary is missing, return the DEGRADED note."""
        if not self.is_enabled(name):
            return None
        missing = self.missing_binaries(name)
        if not missing:
            return None
        return missing_binary_reason(missing[0])

    def pack_support(self, name: str) -> tuple[str, str]:
        """Return ``(support, note)`` for ``vaani caps`` pack rows.

        - disabled → unsupported / "pack disabled"
        - enabled + missing binary → degraded / "{bin} isn't installed"
        - enabled + ok → supported / ""
        """
        if name not in self._descriptors:
            raise PackError(f"unknown pack: {name}")
        if not self.is_enabled(name):
            return "unsupported", "pack disabled"
        reason = self.degraded_reason(name)
        if reason:
            return "degraded", reason
        return "supported", ""

    def apply(self, registry: Registry) -> None:
        """Push enablement + missing-binary notes into the verb ``Registry``."""
        disabled = {
            name
            for name in self._descriptors
            if not self.is_enabled(name)
        }
        notes = {
            name: reason
            for name in self._descriptors
            if (reason := self.degraded_reason(name)) is not None
        }
        registry.set_pack_state(disabled, notes)

    def caps_payload(self) -> dict[str, dict[str, object]]:
        """Machine-readable pack section for ``vaani caps --json``."""
        out: dict[str, dict[str, object]] = {}
        for desc in self.descriptors():
            support, note = self.pack_support(desc.name)
            row: dict[str, object] = {
                "enabled": self.is_enabled(desc.name),
                "always_on": desc.always_on,
                "binaries": list(desc.binaries),
                "auth": list(desc.auth),
                "permissions": sorted(desc.permissions),
                "support": support,
            }
            if note:
                row["note"] = note
            out[desc.name] = row
        return out

    def load(self) -> None:
        target = self._path
        if not target.is_file():
            self._overrides = {}
            return
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._overrides = {}
            return
        enabled = raw.get("enabled") if isinstance(raw, dict) else None
        if not isinstance(enabled, dict):
            self._overrides = {}
            return
        overrides: dict[str, bool] = {}
        for name, value in enabled.items():
            if not isinstance(name, str):
                continue
            desc = self._descriptors.get(name)
            if desc is None or desc.always_on:
                continue
            overrides[name] = bool(value)
        self._overrides = overrides

    def save(self) -> None:
        write_packs_file(self._path, self._overrides)


def write_packs_file(path: Path | str, enabled: Mapping[str, bool]) -> None:
    """Atomically write ``packs.json`` (tmp + ``os.replace``)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Persist only installable overrides; always-on packs are implicit.
    payload = {
        "enabled": {
            name: bool(value)
            for name, value in sorted(enabled.items())
            if name in INSTALLABLE_PACKS
        }
    }
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, target)
    try:
        target.chmod(0o600)
    except OSError:
        pass


def read_packs_file(path: Path | str) -> dict[str, bool]:
    """Read enabled overrides from ``packs.json`` (empty dict if missing/invalid)."""
    reg = PackRegistry(path)
    return dict(reg._overrides)


def register_stub_packs(registry: Registry) -> None:
    """Reserve installable pack names until T4.2–T4.4 land verb handlers.

    No verbs are registered; descriptors alone drive ``vaani caps`` pack rows.
    """
    from vaani.verbs.packs.docker import register_docker_pack
    from vaani.verbs.packs.forge import register_forge_pack
    from vaani.verbs.packs.git import register_git_pack
    from vaani.verbs.packs.pkg import register_pkg_pack

    register_git_pack(registry)
    register_pkg_pack(registry)
    register_docker_pack(registry)
    register_forge_pack(registry)
