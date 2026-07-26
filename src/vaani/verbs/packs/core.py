"""Core pack: today's app / site / browser / agent assistant behavior as verbs."""
from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from vaani.apps import APPS
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
from vaani.sites import PUBLIC_SITES
from vaani.verbs.registry import Registry

# Exact allowlist from the former Controller._browser_intent (plus natural variants).
BROWSER_PHRASES: tuple[str, ...] = (
    "open chrome",
    "open google chrome",
    "open browser",
    "open a browser",
    "open the browser",
    "launch chrome",
    "launch browser",
    "launch a browser",
    "launch the browser",
    "open brave",
    "launch brave",
    "open brave browser",
    "launch brave browser",
    "open a chrome",
    "open the chrome",
    "open a brave",
    "open the brave",
)

_APP_EXCLUDE: tuple[str, ...] = (
    " website",
    " web app",
    " in brave",
    " in chrome",
    " browser",
)

_APP_ACTIONS: tuple[str, ...] = ("open", "launch", "start", "show")
_SITE_ACTIONS: tuple[str, ...] = ("open", "launch", "go to", "show")

_ALL_SUPPORT = {
    PlatformId.LINUX: Support.SUPPORTED,
    PlatformId.MACOS: Support.SUPPORTED,
    PlatformId.WINDOWS: Support.SUPPORTED,
}


def browser_intent(text: str) -> bool:
    """True when normalized text is an exact browser-open allowlist phrase."""
    from vaani.intent.normalize import normalize

    return normalize(text) in BROWSER_PHRASES


def core_patterns() -> tuple[Pattern, ...]:
    """Declarative patterns for corpus/grammar tests (router still uses resolvers)."""
    patterns: list[Pattern] = []

    for aliases, target in APPS:
        patterns.append(
            Pattern(
                verb="app.open",
                any_of=(_APP_ACTIONS, aliases),
                exclude=_APP_EXCLUDE,
                slots=(SlotRule(name="name", value=target.name),),
                priority=20,
            )
        )

    for aliases, name, url in PUBLIC_SITES:
        patterns.append(
            Pattern(
                verb="site.open",
                any_of=(_SITE_ACTIONS, aliases),
                slots=(
                    SlotRule(name="name", value=name),
                    SlotRule(name="url", value=url),
                ),
                priority=10,
            )
        )

    patterns.append(
        Pattern(
            verb="browser.open",
            any_of=(BROWSER_PHRASES,),
            exact=True,
            priority=5,
            fixed_slots={"url": "about:blank"},
        )
    )
    return tuple(patterns)


def build_core_verbs(
    *,
    resolve_app_fn: Callable[[str], Any],
    launch_app_fn: Callable[[Any], str],
    resolve_site_fn: Callable[[str], Any],
    open_browser_fn: Callable[..., str],
    get_app_launcher: Callable[[], Any] | None = None,
    get_browser_launcher: Callable[[], Any] | None = None,
    get_codex: Callable[[], Any] | None = None,
    get_result_window: Callable[[], Any] | None = None,
) -> tuple[Verb, ...]:
    """Build the four migration verbs with injected I/O seams."""

    def _show_text(message: str) -> None:
        window = get_result_window() if get_result_window is not None else None
        if window is not None and hasattr(window, "show_text"):
            window.show_text(message)

    def handle_app_open(intent: Intent, _context: Context) -> Result:
        target = intent.slots.get("target")
        if target is None:
            launcher = get_app_launcher() if get_app_launcher is not None else None
            if launcher is not None:
                target = launcher.resolve(intent.raw_utterance)
            else:
                target = resolve_app_fn(intent.raw_utterance)
        if target is None:
            return Result(status=Status.FAILED, summary="No app matched.", rung=1)
        launcher = get_app_launcher() if get_app_launcher is not None else None
        if launcher is not None:
            answer = launcher.launch(target)
        else:
            answer = launch_app_fn(target)
        _show_text(answer)
        return Result(status=Status.OK, summary=answer, detail=answer, rung=1)

    def handle_site_open(intent: Intent, _context: Context) -> Result:
        url = str(intent.slots.get("url") or "about:blank")
        name = intent.slots.get("name")
        requested = intent.slots.get("browser")
        raw = intent.raw_utterance
        if requested is None:
            folded = raw.casefold()
            requested = "chrome" if "chrome" in folded else "brave"
        prefer = "chrome" if requested == "chrome" else "brave"
        launcher = get_browser_launcher() if get_browser_launcher is not None else None
        if launcher is not None:
            answer = launcher.open(url, prefer=prefer)
        else:
            answer = open_browser_fn(prefer_brave=prefer != "chrome", url=url)
        if name and answer.startswith("Opened"):
            answer = f"Opened {name}."
        _show_text(answer)
        return Result(status=Status.OK, summary=answer, detail=answer, rung=1)

    def handle_browser_open(intent: Intent, _context: Context) -> Result:
        raw = intent.raw_utterance
        prefer = "chrome" if "chrome" in raw.casefold() else "brave"
        url = str(intent.slots.get("url") or "about:blank")
        launcher = get_browser_launcher() if get_browser_launcher is not None else None
        if launcher is not None:
            answer = launcher.open(url, prefer=prefer)
        else:
            answer = open_browser_fn(prefer_brave=prefer != "chrome", url=url)
        _show_text(answer)
        return Result(status=Status.OK, summary=answer, detail=answer, rung=1)

    def handle_agent_task(intent: Intent, _context: Context) -> Result:
        codex = get_codex() if get_codex is not None else None
        if codex is None:
            return Result(
                status=Status.FAILED,
                summary="assistant runner unavailable",
                detail="assistant runner unavailable",
                rung=6,
            )
        prompt = str(intent.slots.get("prompt") or intent.raw_utterance)
        answer = codex.run(prompt)
        window = get_result_window() if get_result_window is not None else None
        if window is not None and hasattr(window, "show"):
            window.show(answer)
        if getattr(answer, "cancelled", False) or getattr(answer, "timed_out", False):
            return Result(
                status=Status.FAILED,
                summary="assistant request cancelled or timed out",
                detail="assistant request cancelled or timed out",
                rung=6,
            )
        final = getattr(answer, "stdout", "") or ""
        return Result(status=Status.OK, summary=final, detail=final, rung=6)

    return (
        Verb(
            name="app.open",
            title="Open an application",
            slots={"name": SlotSpec(type="str", required=True)},
            rung=1,
            risk=RiskClass.R0,
            requires=frozenset(),
            support=_ALL_SUPPORT,
            undo=None,
            pack="core",
            handler=handle_app_open,
        ),
        Verb(
            name="site.open",
            title="Open a website",
            slots={"url": SlotSpec(type="str", required=True)},
            rung=1,
            risk=RiskClass.R0,
            requires=frozenset(),
            support=_ALL_SUPPORT,
            undo=None,
            pack="core",
            handler=handle_site_open,
        ),
        Verb(
            name="browser.open",
            title="Open a browser",
            slots={},
            rung=1,
            risk=RiskClass.R0,
            requires=frozenset(),
            support=_ALL_SUPPORT,
            undo=None,
            pack="core",
            handler=handle_browser_open,
        ),
        Verb(
            name="agent.task",
            title="Run an agent task",
            slots={"prompt": SlotSpec(type="str", required=True)},
            rung=6,
            risk=RiskClass.R2,
            requires=frozenset(),
            support=_ALL_SUPPORT,
            undo=None,
            pack="core",
            handler=handle_agent_task,
        ),
    )


def build_core_registry(
    *,
    resolve_app_fn: Callable[[str], Any],
    launch_app_fn: Callable[[Any], str],
    resolve_site_fn: Callable[[str], Any],
    open_browser_fn: Callable[..., str],
    get_app_launcher: Callable[[], Any] | None = None,
    get_browser_launcher: Callable[[], Any] | None = None,
    get_codex: Callable[[], Any] | None = None,
    get_result_window: Callable[[], Any] | None = None,
    patterns: Sequence[Pattern] | None = None,
) -> tuple[Registry, tuple[Pattern, ...]]:
    """Register core verbs and return ``(registry, patterns)``."""
    registry = Registry()
    for verb in build_core_verbs(
        resolve_app_fn=resolve_app_fn,
        launch_app_fn=launch_app_fn,
        resolve_site_fn=resolve_site_fn,
        open_browser_fn=open_browser_fn,
        get_app_launcher=get_app_launcher,
        get_browser_launcher=get_browser_launcher,
        get_codex=get_codex,
        get_result_window=get_result_window,
    ):
        registry.register(verb)
    return registry, tuple(patterns) if patterns is not None else core_patterns()
