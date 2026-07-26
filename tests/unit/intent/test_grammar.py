"""Grammar pattern matching (T0.4)."""
from __future__ import annotations

from vaani.intent.grammar import Pattern, SlotRule, match
from vaani.verbs.packs.core import BROWSER_PHRASES, core_patterns


def test_exact_browser_allowlist_and_negative() -> None:
    patterns = (
        Pattern(
            verb="browser.open",
            any_of=(BROWSER_PHRASES,),
            exact=True,
            priority=5,
        ),
    )
    assert match("Open a browser", patterns)[0] == "browser.open"
    assert match("open chrome and run ls", patterns) is None


def test_app_vs_site_precedence_via_patterns() -> None:
    patterns = core_patterns()
    app = match("open Claude", patterns)
    site = match("open Claude website", patterns)
    assert app is not None and app[0] == "app.open"
    assert app[1]["name"] == "Claude"
    assert site is not None and site[0] == "site.open"
    assert site[1]["name"] == "Claude"


def test_priority_breaks_ties() -> None:
    patterns = (
        Pattern(verb="low", any_of=(("hello",),), priority=1),
        Pattern(verb="high", any_of=(("hello",),), priority=9),
    )
    assert match("hello there", patterns)[0] == "high"


def test_slot_rule_constant_and_regex() -> None:
    patterns = (
        Pattern(
            verb="demo",
            any_of=(("free port",),),
            slots=(
                SlotRule(name="kind", value="tcp"),
                SlotRule(name="port", regex=r"port\s+(\d+)"),
            ),
            priority=1,
        ),
    )
    verb, slots, _ = match("free port 3000", patterns)
    assert verb == "demo"
    assert slots == {"kind": "tcp", "port": "3000"}
