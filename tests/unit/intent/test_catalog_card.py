"""Unit tests for OS-filtered catalog card (LLM parse prompt)."""
from __future__ import annotations

from vaani.intent.catalog_card import build_catalog_card
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
from vaani.verbs.registry import Registry


def _ok(_intent: Intent, _context: Context) -> Result:
    return Result(status=Status.OK, summary="ok")


def _verb(
    name: str,
    *,
    title: str = "Title",
    slots: dict[str, SlotSpec] | None = None,
    risk: RiskClass = RiskClass.R0,
    rung: int = 1,
    support: dict[PlatformId, Support] | None = None,
    pack: str = "core",
) -> Verb:
    return Verb(
        name=name,
        title=title,
        slots=slots
        or {
            "query": SlotSpec(type="str"),
            "browser": SlotSpec(type="str", required=False),
        },
        rung=rung,
        risk=risk,
        requires=frozenset(),
        support=support
        or {
            PlatformId.LINUX: Support.SUPPORTED,
            PlatformId.MACOS: Support.SUPPORTED,
            PlatformId.WINDOWS: Support.SUPPORTED,
        },
        undo=None,
        pack=pack,
        handler=_ok,
    )


def test_catalog_card_lists_site_search_omits_agent_task() -> None:
    registry = Registry()
    registry.register(
        _verb(
            "site.search",
            title="Search the web",
            risk=RiskClass.R0,
            rung=1,
        )
    )
    registry.register(
        _verb(
            "agent.task",
            title="Run agent task",
            slots={"prompt": SlotSpec(type="str")},
            risk=RiskClass.R2,
            rung=6,
        )
    )

    card = build_catalog_card(registry, PlatformId.MACOS)

    assert card["platform"] == "macos"
    assert isinstance(card["platform_notes"], str)
    assert card["platform_notes"].strip()
    names = [v["name"] for v in card["verbs"]]
    assert names == ["site.search"]
    assert "agent.task" not in names

    entry = card["verbs"][0]
    assert entry["title"] == "Search the web"
    assert entry["slots"] == {"query": "str", "browser": "str?"}
    assert entry["risk"] == "R0"
    assert entry["rung"] == 1
    assert entry["support"] == "supported"


def test_catalog_card_os_filters_unsupported() -> None:
    registry = Registry()
    registry.register(
        _verb(
            "window.focus",
            title="Focus a window",
            slots={"name": SlotSpec(type="str")},
            support={
                PlatformId.LINUX: Support.SUPPORTED,
                PlatformId.MACOS: Support.SUPPORTED,
                PlatformId.WINDOWS: Support.UNSUPPORTED,
            },
        )
    )
    registry.register(
        _verb(
            "site.search",
            title="Search the web",
            support={
                PlatformId.LINUX: Support.SUPPORTED,
                PlatformId.MACOS: Support.SUPPORTED,
                PlatformId.WINDOWS: Support.SUPPORTED,
            },
        )
    )

    windows = build_catalog_card(registry, PlatformId.WINDOWS)
    macos = build_catalog_card(registry, PlatformId.MACOS)

    assert [v["name"] for v in windows["verbs"]] == ["site.search"]
    assert {v["name"] for v in macos["verbs"]} == {"window.focus", "site.search"}
    assert windows["platform"] == "windows"
    assert windows["platform_notes"].strip()
    assert macos["platform_notes"].strip()
    assert windows["platform_notes"] != macos["platform_notes"]


def test_catalog_card_platform_notes_for_all_oses() -> None:
    registry = Registry()
    for platform in (PlatformId.LINUX, PlatformId.MACOS, PlatformId.WINDOWS):
        card = build_catalog_card(registry, platform)
        assert card["platform"] == platform.value
        assert card["platform_notes"].strip()
        assert card["verbs"] == []
