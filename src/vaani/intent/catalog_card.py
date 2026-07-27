"""Compact OS-filtered verb catalog for the parse LLM prompt."""
from __future__ import annotations

from typing import Any, Mapping

from vaani.intent.schema import SlotSpec, Support
from vaani.platform.protocol import PlatformId
from vaani.verbs.registry import Registry

_AGENT_TASK = "agent.task"

_PLATFORM_NOTES: dict[PlatformId, str] = {
    PlatformId.MACOS: (
        "Use macOS app names (Google Chrome, Terminal, Brave Browser). "
        "Prefer app.open/window.focus over shell. "
        "On-screen where/what → guide.point. "
        "Open the first result/site/link (ASR: first side) → browser.result.open, "
        "never site.search for that ask."
    ),
    PlatformId.WINDOWS: (
        "Use Windows app names (cmd, powershell, Microsoft Edge, Chrome). "
        "Prefer app.open/window.focus over shell. "
        "On-screen where/what → guide.point. "
        "Open the first result/site/link (ASR: first side) → browser.result.open, "
        "never site.search for that ask."
    ),
    PlatformId.LINUX: (
        "Use Linux desktop apps (firefox, google-chrome, nautilus, gnome-terminal). "
        "Prefer app.open/xdg-open patterns over raw shell. "
        "On-screen where/what → guide.point. "
        "Open the first result/site/link (ASR: first side) → browser.result.open, "
        "never site.search for that ask."
    ),
}


def build_catalog_card(registry: Registry, platform: PlatformId) -> dict[str, Any]:
    """Build prompt JSON: platform, platform_notes, and enabled verbs (no agent.task)."""
    verbs: list[dict[str, Any]] = []
    for verb in registry.enabled(platform):
        if verb.name == _AGENT_TASK:
            continue
        support = verb.support.get(platform, Support.UNSUPPORTED)
        verbs.append(
            {
                "name": verb.name,
                "title": verb.title,
                "slots": _slot_types(verb.slots),
                "risk": verb.risk.value,
                "rung": verb.rung,
                "support": support.value,
            }
        )
    return {
        "platform": platform.value,
        "platform_notes": _PLATFORM_NOTES[platform],
        "verbs": verbs,
    }


def _slot_types(slots: Mapping[str, SlotSpec]) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, spec in slots.items():
        out[name] = spec.type if spec.required else f"{spec.type}?"
    return out
