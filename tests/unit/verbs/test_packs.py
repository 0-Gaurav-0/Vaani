"""Capability pack registry tests (T4.1)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from vaani.intent.schema import (
    Context,
    Intent,
    Result,
    RiskClass,
    SlotSpec,
    Status,
    Support,
    Verb,
)
from vaani.platform.protocol import PlatformId
from vaani.verbs.packs.registry import (
    ALWAYS_ON_PACKS,
    BUILTIN_PACKS,
    INSTALLABLE_PACKS,
    PackError,
    PackPrerequisiteError,
    PackRegistry,
    missing_binary_reason,
)
from vaani.verbs.registry import Registry


def _ok(_intent: Intent, _context: Context) -> Result:
    return Result(status=Status.OK, summary="ok")


def _forge_verb() -> Verb:
    return Verb(
        name="forge.pr.create",
        title="Create a pull request",
        slots={},
        rung=4,
        risk=RiskClass.R2,
        requires=frozenset(),
        support={
            PlatformId.LINUX: Support.SUPPORTED,
            PlatformId.MACOS: Support.SUPPORTED,
            PlatformId.WINDOWS: Support.SUPPORTED,
        },
        undo=None,
        pack="forge",
        handler=_ok,
    )


def test_core_cannot_be_disabled(tmp_path: Path) -> None:
    packs = PackRegistry(tmp_path / "packs.json", which=lambda _b: "/bin/true")
    for name in ALWAYS_ON_PACKS:
        assert packs.is_enabled(name) is True
        with pytest.raises(PackError, match="always on"):
            packs.set_enabled(name, False)
        assert packs.is_enabled(name) is True


def test_toggle_installable_pack_persists(tmp_path: Path) -> None:
    path = tmp_path / "packs.json"
    packs = PackRegistry(path, which=lambda _b: "/usr/bin/git")
    assert packs.is_enabled("git") is False
    packs.set_enabled("git", True)
    assert packs.is_enabled("git") is True
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw == {"enabled": {"git": True}}

    reloaded = PackRegistry(path, which=lambda _b: "/usr/bin/git")
    assert reloaded.is_enabled("git") is True
    reloaded.set_enabled("git", False)
    assert PackRegistry(path, which=lambda _b: "/usr/bin/git").is_enabled("git") is False


def test_missing_binary_degraded_message(tmp_path: Path) -> None:
    which_map = {"git": "/usr/bin/git"}

    def which(name: str) -> str | None:
        return which_map.get(name)

    packs = PackRegistry(tmp_path / "packs.json", which=which)
    packs.set_enabled("forge", True)
    assert packs.degraded_reason("forge") == "gh isn't installed"
    support, note = packs.pack_support("forge")
    assert support == "degraded"
    assert note == "gh isn't installed"

    with pytest.raises(PackPrerequisiteError, match="gh isn't installed") as exc:
        packs.require_binary("gh")
    assert exc.value.binary == "gh"
    assert missing_binary_reason("gh") == "gh isn't installed"


def test_require_binary_returns_path(tmp_path: Path) -> None:
    packs = PackRegistry(
        tmp_path / "packs.json",
        which=lambda name: "/usr/bin/gh" if name == "gh" else None,
    )
    assert packs.require_binary("gh") == "/usr/bin/gh"


def test_disabled_pack_unsupported_not_enabled(tmp_path: Path) -> None:
    packs = PackRegistry(tmp_path / "packs.json", which=lambda _b: "/usr/bin/gh")
    assert packs.is_enabled("forge") is False
    support, note = packs.pack_support("forge")
    assert support == "unsupported"
    assert note == "pack disabled"

    registry = Registry()
    registry.register(_forge_verb())
    packs.apply(registry)
    assert registry.enabled(PlatformId.LINUX) == ()
    support_cell, note_cell = registry.matrix()["forge.pr.create"]["linux"]
    assert support_cell is Support.UNSUPPORTED
    assert note_cell == "pack disabled"


def test_enabled_pack_missing_binary_degrades_verb_matrix(tmp_path: Path) -> None:
    packs = PackRegistry(tmp_path / "packs.json", which=lambda _b: None)
    packs.set_enabled("forge", True)
    registry = Registry()
    registry.register(_forge_verb())
    packs.apply(registry)
    # Still enabled (DEGRADED ≠ disabled); matrix carries the binary reason.
    assert [v.name for v in registry.enabled(PlatformId.LINUX)] == ["forge.pr.create"]
    support, note = registry.matrix()["forge.pr.create"]["linux"]
    assert support is Support.DEGRADED
    assert note == "gh isn't installed"


def test_caps_json_shape(tmp_path: Path) -> None:
    which_map = {"git": "/usr/bin/git", "docker": "/usr/bin/docker"}

    def which(name: str) -> str | None:
        return which_map.get(name)

    packs = PackRegistry(tmp_path / "packs.json", which=which)
    packs.set_enabled("git", True)
    packs.set_enabled("forge", True)
    payload = packs.caps_payload()

    assert set(payload) == set(ALWAYS_ON_PACKS) | set(INSTALLABLE_PACKS)
    assert set(payload) == set(BUILTIN_PACKS)

    for name in ALWAYS_ON_PACKS:
        row = payload[name]
        assert row["enabled"] is True
        assert row["always_on"] is True
        assert row["support"] == "supported"
        assert "note" not in row

    assert payload["git"]["enabled"] is True
    assert payload["git"]["support"] == "supported"
    assert payload["git"]["binaries"] == ["git"]

    assert payload["forge"]["enabled"] is True
    assert payload["forge"]["support"] == "degraded"
    assert payload["forge"]["note"] == "gh isn't installed"
    assert payload["forge"]["auth"] == ["gh"]

    assert payload["computer-use"]["enabled"] is False
    assert payload["computer-use"]["support"] == "unsupported"
    assert payload["computer-use"]["note"] == "pack disabled"
    assert "accessibility" in payload["computer-use"]["permissions"]


def test_save_is_atomic(tmp_path: Path) -> None:
    path = tmp_path / "packs.json"
    packs = PackRegistry(path, which=lambda _b: None)
    packs.set_enabled("docker", True)
    assert path.is_file()
    assert not path.with_suffix(".json.tmp").exists()
    assert json.loads(path.read_text(encoding="utf-8"))["enabled"]["docker"] is True
