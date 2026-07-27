"""Always-on browser search-result verbs (structured first-result open)."""
from __future__ import annotations

import logging
import re
import subprocess
from collections.abc import Callable
from typing import Any

from vaani.intent.grammar import Pattern, SlotRule
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

PACK_NAME = "core"
BROWSER_RESULT_VERB_NAMES: frozenset[str] = frozenset({"browser.result.open"})

_ALL_SUPPORT = {
    PlatformId.LINUX: Support.SUPPORTED,
    PlatformId.MACOS: Support.SUPPORTED,
    PlatformId.WINDOWS: Support.SUPPORTED,
}

_LOG = logging.getLogger("vaani.browser_results")

ResolveResultUrl = Callable[[int], str | None]
OpenUrl = Callable[[str, Intent], str]


def browser_result_patterns() -> tuple[Pattern, ...]:
    """Grammar for opening the Nth SERP result (includes ASR “side” for “site”)."""
    return (
        Pattern(
            verb="browser.result.open",
            any_of=(("open",), ("first result", "first site", "first link", "first side")),
            slots=(SlotRule(name="index", value=1),),
            priority=60,
        ),
        Pattern(
            verb="browser.result.open",
            any_of=(("open the first result", "open first result"),),
            slots=(SlotRule(name="index", value=1),),
            priority=60,
            exact=False,
        ),
        Pattern(
            verb="browser.result.open",
            any_of=(("open",), ("result", "site", "link", "side")),
            slots=(
                SlotRule(
                    name="index",
                    regex=r"(?:open\s+(?:the\s+)?)?(?:(\d+)(?:st|nd|rd|th)?|first|second|third)\s+(?:result|site|link|side)",
                ),
            ),
            priority=55,
        ),
    )


def _ordinal_index(raw: Any) -> int:
    if isinstance(raw, int):
        return max(1, raw)
    text = str(raw or "1").strip().casefold()
    words = {"first": 1, "second": 2, "third": 3, "1st": 1, "2nd": 2, "3rd": 3}
    if text in words:
        return words[text]
    digits = re.search(r"\d+", text)
    if digits:
        return max(1, int(digits.group(0)))
    return 1


def _macos_resolve_result_url(index: int) -> str | None:
    """Best-effort: run JS in front Chrome/Safari to pick the Nth http(s) result.

    Fragile Google SERP heuristic — prefer injectable resolvers in tests / CDP later.
    """
    # Prefer organic anchors; skip google internal links.
    js = f"""
    (function() {{
      var n = {int(index)};
      var nodes = Array.prototype.slice.call(
        document.querySelectorAll('a[href^="http"]')
      );
      var hrefs = [];
      for (var i = 0; i < nodes.length; i++) {{
        var href = nodes[i].href || '';
        if (!href) continue;
        if (href.indexOf('google.') !== -1) continue;
        if (href.indexOf('webcache') !== -1) continue;
        hrefs.push(href);
      }}
      return hrefs[n - 1] || '';
    }})();
    """
    for app, cmd in (
        (
            "Google Chrome",
            [
                "osascript",
                "-e",
                f'tell application "Google Chrome" to tell active tab of front window to execute javascript {js!r}',
            ],
        ),
        (
            "Safari",
            [
                "osascript",
                "-e",
                f'tell application "Safari" to do JavaScript {js!r} in front document',
            ],
        ),
    ):
        try:
            completed = subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            _LOG.info("event=browser_result_resolve_fail app=%s err=%s", app, exc)
            continue
        if completed.returncode != 0:
            continue
        url = (completed.stdout or "").strip()
        if url.startswith("http://") or url.startswith("https://"):
            return url
    return None


def _default_open_url(url: str, _intent: Intent) -> str:
    import webbrowser

    webbrowser.open(url)
    return f"Opened {url}"


def build_browser_result_verbs(
    *,
    resolve_result_url: ResolveResultUrl | None = None,
    open_url: OpenUrl | None = None,
) -> tuple[Verb, ...]:
    resolver = resolve_result_url or _macos_resolve_result_url
    opener = open_url or _default_open_url

    def handle_open(intent: Intent, _context: Context) -> Result:
        index = _ordinal_index(intent.slots.get("index", 1))
        url = resolver(index)
        if not url:
            return Result(
                status=Status.PARTIAL,
                summary="Couldn't read results",
                detail=(
                    "couldn't read results; try guide or enable vision click"
                ),
                evidence=("browser.result.open", f"index={index}", "miss"),
                rung=2,
            )
        answer = opener(url, intent)
        return Result(
            status=Status.OK,
            summary=answer if isinstance(answer, str) else f"Opened {url}",
            detail=str(answer),
            evidence=(url,),
            rung=2,
        )

    return (
        Verb(
            name="browser.result.open",
            title="Open Nth search result",
            slots={
                "index": SlotSpec(type="int", required=False, default=1),
            },
            rung=2,
            risk=RiskClass.R2,
            requires=frozenset(),
            support=_ALL_SUPPORT,
            undo=None,
            pack=PACK_NAME,
            handler=handle_open,
        ),
    )


def register_browser_results_pack(
    registry: Registry,
    *,
    resolve_result_url: ResolveResultUrl | None = None,
    open_url: OpenUrl | None = None,
) -> tuple[Pattern, ...]:
    for verb in build_browser_result_verbs(
        resolve_result_url=resolve_result_url,
        open_url=open_url,
    ):
        registry.register(verb)
    return browser_result_patterns()


__all__ = [
    "BROWSER_RESULT_VERB_NAMES",
    "browser_result_patterns",
    "build_browser_result_verbs",
    "register_browser_results_pack",
]
