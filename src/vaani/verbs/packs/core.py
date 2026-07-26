"""Core pack: apps, sites, browser, system, files, and agent verbs."""
from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

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
from vaani.known_folders import canonical_folder, resolve_known_folder
from vaani.platform.protocol import PlatformId
from vaani.sites import PUBLIC_SITES
from vaani.verbs.packs.procs import register_procs_pack
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

PRIVATE_WINDOW_PHRASES: tuple[str, ...] = (
    "open a new incognito window",
    "open new incognito window",
    "open an incognito window",
    "open incognito window",
    "open incognito",
    "open a private window",
    "open private window",
    "open a new private window",
    "open inprivate window",
    "open an inprivate window",
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
_QUIT_ACTIONS: tuple[str, ...] = ("quit", "close")
_FOLDER_NAMES: tuple[str, ...] = (
    "downloads",
    "download",
    "desktop",
    "documents",
    "document",
    "docs",
    "pictures",
    "photos",
    "music",
    "movies",
    "videos",
    "home",
    "home folder",
)

_ALL_SUPPORT = {
    PlatformId.LINUX: Support.SUPPORTED,
    PlatformId.MACOS: Support.SUPPORTED,
    PlatformId.WINDOWS: Support.SUPPORTED,
}

_VOLUME_SUPPORT = {
    PlatformId.LINUX: Support.SUPPORTED,
    PlatformId.MACOS: Support.SUPPORTED,
    PlatformId.WINDOWS: Support.DEGRADED,
}

_DND_SUPPORT = {
    PlatformId.LINUX: Support.DEGRADED,
    PlatformId.MACOS: Support.DEGRADED,
    PlatformId.WINDOWS: Support.DEGRADED,
}

_DISPLAY_SLEEP_SUPPORT = {
    PlatformId.LINUX: Support.DEGRADED,
    PlatformId.MACOS: Support.SUPPORTED,
    PlatformId.WINDOWS: Support.SUPPORTED,
}

CORE_VERB_NAMES: frozenset[str] = frozenset(
    {
        "app.open",
        "app.quit",
        "site.open",
        "site.search",
        "browser.open",
        "browser.window.private",
        "system.volume.set",
        "system.dnd.set",
        "system.lock",
        "system.display.sleep",
        "system.ip.copy",
        "files.reveal",
        "files.open_dir",
        "agent.task",
    }
)

Runner = Callable[..., Any]
Popen = Callable[..., Any]


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

    patterns.extend(
        (
            Pattern(
                verb="app.quit",
                any_of=(_QUIT_ACTIONS,),
                exclude=(" tab", " window"),
                slots=(SlotRule(name="name", regex=r"(?:quit|close)\s+(.+)"),),
                priority=22,
            ),
            Pattern(
                verb="system.volume.set",
                any_of=(("mute", "mute volume", "mute the volume"),),
                exact=True,
                priority=40,
                fixed_slots={"muted": True},
            ),
            Pattern(
                verb="system.volume.set",
                any_of=(("unmute", "unmute volume", "unmute the volume"),),
                exact=True,
                priority=40,
                fixed_slots={"muted": False},
            ),
            Pattern(
                verb="system.volume.set",
                any_of=(("volume", "set volume"),),
                slots=(SlotRule(name="level", regex=r"(\d+)"),),
                priority=35,
            ),
            Pattern(
                verb="system.dnd.set",
                any_of=(
                    (
                        "do not disturb",
                        "dnd",
                        "focus mode",
                    ),
                ),
                require=("on",),
                priority=35,
                fixed_slots={"enabled": True},
            ),
            Pattern(
                verb="system.dnd.set",
                any_of=(
                    (
                        "turn on do not disturb",
                        "enable do not disturb",
                        "turn on dnd",
                        "enable dnd",
                    ),
                ),
                priority=36,
                fixed_slots={"enabled": True},
            ),
            Pattern(
                verb="system.dnd.set",
                any_of=(
                    (
                        "turn off do not disturb",
                        "disable do not disturb",
                        "turn off dnd",
                        "disable dnd",
                    ),
                ),
                priority=36,
                fixed_slots={"enabled": False},
            ),
            Pattern(
                verb="system.lock",
                any_of=(
                    (
                        "lock the screen",
                        "lock screen",
                        "lock my screen",
                        "lock computer",
                        "lock the computer",
                    ),
                ),
                exact=True,
                priority=40,
            ),
            Pattern(
                verb="system.display.sleep",
                any_of=(
                    (
                        "sleep the display",
                        "sleep display",
                        "turn off the display",
                        "turn off display",
                        "display sleep",
                    ),
                ),
                exact=True,
                priority=40,
            ),
            Pattern(
                verb="system.ip.copy",
                any_of=(
                    (
                        "copy my local ip address",
                        "copy my local ip",
                        "copy local ip address",
                        "copy local ip",
                        "copy my ip address",
                        "copy my ip",
                    ),
                ),
                exact=True,
                priority=40,
            ),
            Pattern(
                verb="files.reveal",
                any_of=(("reveal", "show"),),
                require=("project",),
                priority=30,
            ),
            Pattern(
                verb="files.open_dir",
                any_of=(_APP_ACTIONS, _FOLDER_NAMES),
                slots=(SlotRule(name="folder", from_group=1),),
                priority=25,
            ),
            Pattern(
                verb="site.open",
                any_of=(("localhost", "local host"),),
                slots=(
                    SlotRule(name="port", regex=r"(?:localhost|local host)\s+(\d+)"),
                    SlotRule(name="name", value="localhost"),
                ),
                priority=35,
            ),
            Pattern(
                verb="site.search",
                any_of=(
                    (
                        "search google for",
                        "google search for",
                        "search for",
                    ),
                ),
                slots=(
                    SlotRule(
                        name="query",
                        regex=(
                            r"(?:search\s+google\s+for|google\s+search\s+for|"
                            r"search\s+for)\s+(.+)"
                        ),
                    ),
                    SlotRule(name="engine", value="google"),
                ),
                priority=45,
            ),
            Pattern(
                verb="browser.window.private",
                any_of=(PRIVATE_WINDOW_PHRASES,),
                exact=True,
                priority=40,
            ),
        )
    )
    return tuple(patterns)


def _missing_system(action: str, *, rung: int) -> Result:
    return Result(
        status=Status.UNSUPPORTED,
        summary=f"{action} unavailable",
        detail="SystemControl is not available on this platform bundle",
        rung=rung,
    )


def _prefer_browser(raw: str, requested: Any | None = None) -> str:
    if requested is not None:
        return "chrome" if str(requested).casefold() == "chrome" else "brave"
    folded = raw.casefold()
    return "chrome" if "chrome" in folded else "brave"


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
    get_system: Callable[[], Any | None] | None = None,
    get_delivery: Callable[[], Any | None] | None = None,
    get_platform: Callable[[], PlatformId] | None = None,
    quit_app_fn: Callable[[str, PlatformId], Result] | None = None,
    open_private_fn: Callable[..., str] | None = None,
    reveal_fn: Callable[[Path, PlatformId], Result] | None = None,
    open_dir_fn: Callable[[Path, PlatformId], Result] | None = None,
    resolve_folder_fn: Callable[..., Path | None] | None = None,
    runner: Runner | None = None,
    popen: Popen | None = None,
) -> tuple[Verb, ...]:
    """Build core verbs with injected I/O seams."""

    run = runner or subprocess.run
    spawn = popen or subprocess.Popen
    platform_of = get_platform or (lambda: PlatformId.LINUX)
    folder_resolve = resolve_folder_fn or resolve_known_folder

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

    def handle_app_quit(intent: Intent, context: Context) -> Result:
        name = str(intent.slots.get("name") or "").strip()
        if not name:
            return Result(
                status=Status.FAILED,
                summary="No app named to quit.",
                detail="app.quit requires a name slot",
                rung=1,
            )
        platform = context.platform if context.platform else platform_of()
        if quit_app_fn is not None:
            return quit_app_fn(name, platform)
        return _quit_app(name, platform, runner=run)

    def handle_site_open(intent: Intent, _context: Context) -> Result:
        port = intent.slots.get("port")
        url = intent.slots.get("url")
        if port is not None and not url:
            url = f"http://localhost:{port}"
        url = str(url or "about:blank")
        name = intent.slots.get("name")
        requested = intent.slots.get("browser")
        prefer = _prefer_browser(intent.raw_utterance, requested)
        launcher = get_browser_launcher() if get_browser_launcher is not None else None
        if launcher is not None:
            answer = launcher.open(url, prefer=prefer)
        else:
            answer = open_browser_fn(prefer_brave=prefer != "chrome", url=url)
        if name and answer.startswith("Opened"):
            answer = f"Opened {name}."
        _show_text(answer)
        return Result(status=Status.OK, summary=answer, detail=answer, rung=1)

    def handle_site_search(intent: Intent, _context: Context) -> Result:
        query = str(intent.slots.get("query") or "").strip()
        if not query:
            return Result(
                status=Status.FAILED,
                summary="No search query.",
                detail="site.search requires a query slot",
                rung=2,
            )
        engine = str(intent.slots.get("engine") or "google").casefold()
        if engine != "google":
            return Result(
                status=Status.UNSUPPORTED,
                summary=f"Search engine {engine} unsupported",
                detail="Only Google search is wired in the core pack",
                rung=2,
            )
        url = f"https://www.google.com/search?q={quote_plus(query)}"
        prefer = _prefer_browser(intent.raw_utterance, intent.slots.get("browser"))
        launcher = get_browser_launcher() if get_browser_launcher is not None else None
        if launcher is not None:
            answer = launcher.open(url, prefer=prefer)
        else:
            answer = open_browser_fn(prefer_brave=prefer != "chrome", url=url)
        summary = f"Searched Google for {query}."
        _show_text(summary)
        return Result(
            status=Status.OK,
            summary=summary,
            detail=answer,
            evidence=(url,),
            rung=2,
        )

    def handle_browser_open(intent: Intent, _context: Context) -> Result:
        prefer = _prefer_browser(intent.raw_utterance)
        url = str(intent.slots.get("url") or "about:blank")
        launcher = get_browser_launcher() if get_browser_launcher is not None else None
        if launcher is not None:
            answer = launcher.open(url, prefer=prefer)
        else:
            answer = open_browser_fn(prefer_brave=prefer != "chrome", url=url)
        _show_text(answer)
        return Result(status=Status.OK, summary=answer, detail=answer, rung=1)

    def handle_browser_private(intent: Intent, context: Context) -> Result:
        prefer = _prefer_browser(intent.raw_utterance, intent.slots.get("browser"))
        platform = context.platform if context.platform else platform_of()
        if open_private_fn is not None:
            answer = open_private_fn(prefer=prefer, platform=platform)
        else:
            answer = _open_private_window(
                prefer=prefer,
                platform=platform,
                popen=spawn,
            )
        if answer.startswith("Unable"):
            return Result(
                status=Status.FAILED,
                summary=answer,
                detail=answer,
                rung=1,
            )
        _show_text(answer)
        return Result(status=Status.OK, summary=answer, detail=answer, rung=1)

    def handle_volume(intent: Intent, _context: Context) -> Result:
        system = get_system() if get_system is not None else None
        if system is None:
            return _missing_system("Volume control", rung=2)
        if "muted" in intent.slots:
            return system.mute(bool(intent.slots["muted"]))
        level_raw = intent.slots.get("level")
        if level_raw is None:
            return Result(
                status=Status.FAILED,
                summary="No volume level.",
                detail="system.volume.set requires level or muted",
                rung=2,
            )
        try:
            level = int(level_raw)
        except (TypeError, ValueError):
            return Result(
                status=Status.FAILED,
                summary="Invalid volume level.",
                detail=f"not an integer: {level_raw!r}",
                rung=2,
            )
        return system.volume_set(level)

    def handle_dnd(intent: Intent, _context: Context) -> Result:
        system = get_system() if get_system is not None else None
        if system is None:
            return _missing_system("Do Not Disturb", rung=1)
        enabled = bool(intent.slots.get("enabled", True))
        return system.dnd(enabled)

    def handle_lock(_intent: Intent, _context: Context) -> Result:
        system = get_system() if get_system is not None else None
        if system is None:
            return _missing_system("Screen lock", rung=1)
        return system.lock()

    def handle_display_sleep(_intent: Intent, _context: Context) -> Result:
        system = get_system() if get_system is not None else None
        if system is None:
            return _missing_system("Display sleep", rung=1)
        return system.display_sleep()

    def handle_ip_copy(_intent: Intent, _context: Context) -> Result:
        system = get_system() if get_system is not None else None
        if system is None:
            return _missing_system("Local IP", rung=1)
        delivery = get_delivery() if get_delivery is not None else None
        if delivery is None:
            return Result(
                status=Status.UNSUPPORTED,
                summary="Clipboard delivery unavailable",
                detail="system.ip.copy requires bundle.delivery",
                rung=1,
            )
        ip = str(system.local_ip() or "").strip()
        if not ip:
            return Result(
                status=Status.FAILED,
                summary="Could not determine local IP",
                detail="SystemControl.local_ip returned empty",
                rung=1,
            )
        try:
            status = delivery.deliver(ip)
        except TypeError:
            status = delivery.deliver(ip, snapshot=None)
        status_value = getattr(status, "value", str(status)).casefold()
        if status_value == "failed":
            return Result(
                status=Status.FAILED,
                summary="Could not copy IP to clipboard",
                detail=status_value,
                evidence=(ip,),
                rung=1,
            )
        return Result(
            status=Status.OK,
            summary=f"Copied {ip}",
            detail=ip,
            evidence=(ip, status_value),
            rung=1,
        )

    def handle_reveal(_intent: Intent, context: Context) -> Result:
        workspace = context.workspace
        if workspace is None:
            return Result(
                status=Status.FAILED,
                summary="No workspace to reveal",
                detail="files.reveal requires context.workspace",
                rung=3,
            )
        path = Path(workspace)
        platform = context.platform if context.platform else platform_of()
        if reveal_fn is not None:
            return reveal_fn(path, platform)
        return _reveal_path(path, platform, runner=run, popen=spawn)

    def handle_open_dir(intent: Intent, context: Context) -> Result:
        folder = str(intent.slots.get("folder") or intent.slots.get("name") or "").strip()
        if not folder or canonical_folder(folder) is None:
            return Result(
                status=Status.FAILED,
                summary="Unknown folder.",
                detail=f"not a known folder: {folder!r}",
                rung=1,
            )
        platform = context.platform if context.platform else platform_of()
        path = folder_resolve(folder, platform)
        if path is None:
            return Result(
                status=Status.UNSUPPORTED,
                summary=f"Could not resolve {folder}",
                detail="known-folder resolution failed for this OS",
                rung=1,
            )
        if open_dir_fn is not None:
            return open_dir_fn(path, platform)
        return _open_dir(path, platform, runner=run, popen=spawn)

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
            name="app.quit",
            title="Quit an application",
            slots={"name": SlotSpec(type="str", required=True)},
            rung=1,
            risk=RiskClass.R2,
            requires=frozenset(),
            support=_ALL_SUPPORT,
            undo=None,
            pack="core",
            handler=handle_app_quit,
        ),
        Verb(
            name="site.open",
            title="Open a website",
            slots={"url": SlotSpec(type="str", required=False)},
            rung=1,
            risk=RiskClass.R0,
            requires=frozenset(),
            support=_ALL_SUPPORT,
            undo=None,
            pack="core",
            handler=handle_site_open,
        ),
        Verb(
            name="site.search",
            title="Search the web",
            slots={"query": SlotSpec(type="str", required=True)},
            rung=2,
            risk=RiskClass.R0,
            requires=frozenset(),
            support=_ALL_SUPPORT,
            undo=None,
            pack="core",
            handler=handle_site_search,
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
            name="browser.window.private",
            title="Open a private browser window",
            slots={},
            rung=1,
            risk=RiskClass.R0,
            requires=frozenset(),
            support=_ALL_SUPPORT,
            undo=None,
            pack="core",
            handler=handle_browser_private,
        ),
        Verb(
            name="system.volume.set",
            title="Set system volume or mute",
            slots={
                "level": SlotSpec(type="int", required=False),
                "muted": SlotSpec(type="bool", required=False),
            },
            rung=2,
            risk=RiskClass.R0,
            requires=frozenset(),
            support=_VOLUME_SUPPORT,
            undo="system.volume.set",
            pack="core",
            handler=handle_volume,
        ),
        Verb(
            name="system.dnd.set",
            title="Toggle Do Not Disturb",
            slots={"enabled": SlotSpec(type="bool", required=True)},
            rung=1,
            risk=RiskClass.R0,
            requires=frozenset(),
            support=_DND_SUPPORT,
            undo="system.dnd.set",
            pack="core",
            handler=handle_dnd,
        ),
        Verb(
            name="system.lock",
            title="Lock the screen",
            slots={},
            rung=1,
            risk=RiskClass.R0,
            requires=frozenset(),
            support=_ALL_SUPPORT,
            undo=None,
            pack="core",
            handler=handle_lock,
        ),
        Verb(
            name="system.display.sleep",
            title="Sleep the display",
            slots={},
            rung=1,
            risk=RiskClass.R0,
            requires=frozenset(),
            support=_DISPLAY_SLEEP_SUPPORT,
            undo=None,
            pack="core",
            handler=handle_display_sleep,
        ),
        Verb(
            name="system.ip.copy",
            title="Copy local IP to clipboard",
            slots={},
            rung=1,
            risk=RiskClass.R0,
            requires=frozenset(),
            support=_ALL_SUPPORT,
            undo=None,
            pack="core",
            handler=handle_ip_copy,
        ),
        Verb(
            name="files.reveal",
            title="Reveal workspace in file manager",
            slots={},
            rung=3,
            risk=RiskClass.R0,
            requires=frozenset({"workspace"}),
            support=_ALL_SUPPORT,
            undo=None,
            pack="core",
            handler=handle_reveal,
        ),
        Verb(
            name="files.open_dir",
            title="Open a known folder",
            slots={"folder": SlotSpec(type="str", required=True)},
            rung=1,
            risk=RiskClass.R0,
            requires=frozenset(),
            support=_ALL_SUPPORT,
            undo=None,
            pack="core",
            handler=handle_open_dir,
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


def _quit_app(name: str, platform: PlatformId, *, runner: Runner) -> Result:
    app = name.strip()
    if platform is PlatformId.MACOS:
        argv = ("osascript", "-e", f'tell application "{app}" to quit')
    elif platform is PlatformId.WINDOWS:
        # Argv-only graceful-ish quit (avoids L3→L5 PowerShell helper import).
        argv = ("taskkill", "/IM", f"{app}.exe")
    else:
        if shutil.which("wmctrl"):
            argv = ("wmctrl", "-c", app)
        else:
            argv = ("pkill", "-TERM", "-f", app)
    try:
        completed = runner(
            list(argv),
            check=False,
            capture_output=True,
            text=True,
            timeout=10.0,
        )
    except Exception as exc:
        return Result(
            status=Status.FAILED,
            summary=f"Could not quit {app}",
            detail=str(exc),
            evidence=tuple(argv),
            rung=1,
        )
    code = getattr(completed, "returncode", 1)
    if code != 0:
        detail = (getattr(completed, "stderr", None) or getattr(completed, "stdout", None) or "").strip()
        return Result(
            status=Status.FAILED,
            summary=f"Could not quit {app}",
            detail=detail or f"exit {code}",
            evidence=tuple(argv),
            rung=1,
        )
    return Result(
        status=Status.OK,
        summary=f"Quit {app}",
        detail=f"Quit {app}",
        evidence=tuple(argv),
        rung=1,
    )


def _open_private_window(
    *,
    prefer: str,
    platform: PlatformId,
    popen: Popen,
) -> str:
    prefer_key = "chrome" if prefer == "chrome" else "brave"
    if platform is PlatformId.MACOS:
        apps = (
            ("Google Chrome", "--incognito")
            if prefer_key == "chrome"
            else ("Brave Browser", "--incognito")
        )
        order = [apps]
        order.append(
            ("Brave Browser", "--incognito")
            if prefer_key == "chrome"
            else ("Google Chrome", "--incognito")
        )
        for app_name, flag in order:
            try:
                proc = popen(
                    ["open", "-na", app_name, "--args", flag],
                    start_new_session=True,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                if getattr(proc, "poll", lambda: None)() is None:
                    return "Opened private browser window."
            except OSError:
                continue
        return "Unable to open private browser window."

    flag = "--incognito"
    if prefer_key == "chrome":
        names = (
            "google-chrome",
            "chromium",
            "chromium-browser",
            "chrome",
            "brave-browser",
            "brave",
        )
    else:
        names = (
            "brave-browser",
            "brave",
            "google-chrome",
            "chromium",
            "chromium-browser",
            "chrome",
        )
    if platform is PlatformId.WINDOWS:
        if prefer_key == "chrome":
            names = (
                "chrome.exe",
                "chrome",
                "brave.exe",
                "brave",
                "msedge.exe",
                "msedge",
            )
        else:
            names = (
                "brave.exe",
                "brave",
                "chrome.exe",
                "chrome",
                "msedge.exe",
                "msedge",
            )

    for name in names:
        executable = shutil.which(name)
        if not executable:
            continue
        use_flag = "--inprivate" if "msedge" in name.casefold() else flag
        try:
            proc = popen(
                [executable, use_flag],
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if getattr(proc, "poll", lambda: None)() is None:
                return "Opened private browser window."
        except OSError:
            continue
    return "Unable to open private browser window."


def _reveal_path(
    path: Path,
    platform: PlatformId,
    *,
    runner: Runner,
    popen: Popen,
) -> Result:
    if platform is PlatformId.MACOS:
        argv = ("open", "-R", str(path))
    elif platform is PlatformId.WINDOWS:
        argv = ("explorer", f"/select,{path}")
    else:
        if shutil.which("nautilus"):
            argv = ("nautilus", "--select", str(path))
        else:
            argv = ("xdg-open", str(path if path.is_dir() else path.parent))
    try:
        popen(
            list(argv),
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        return Result(
            status=Status.FAILED,
            summary="Could not reveal workspace",
            detail=str(exc),
            evidence=argv,
            rung=3,
        )
    _ = runner
    return Result(
        status=Status.OK,
        summary=f"Revealed {path.name}",
        detail=str(path),
        evidence=argv,
        rung=3,
    )


def _open_dir(
    path: Path,
    platform: PlatformId,
    *,
    runner: Runner,
    popen: Popen,
) -> Result:
    if platform is PlatformId.MACOS:
        argv = ("open", str(path))
    elif platform is PlatformId.WINDOWS:
        argv = ("explorer", str(path))
    else:
        argv = ("xdg-open", str(path))
    try:
        popen(
            list(argv),
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        return Result(
            status=Status.FAILED,
            summary="Could not open folder",
            detail=str(exc),
            evidence=argv,
            rung=1,
        )
    _ = runner
    return Result(
        status=Status.OK,
        summary=f"Opened {path.name}",
        detail=str(path),
        evidence=argv,
        rung=1,
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
    get_system: Callable[[], Any | None] | None = None,
    get_delivery: Callable[[], Any | None] | None = None,
    get_platform: Callable[[], PlatformId] | None = None,
    quit_app_fn: Callable[[str, PlatformId], Result] | None = None,
    open_private_fn: Callable[..., str] | None = None,
    reveal_fn: Callable[[Path, PlatformId], Result] | None = None,
    open_dir_fn: Callable[[Path, PlatformId], Result] | None = None,
    resolve_folder_fn: Callable[..., Path | None] | None = None,
    runner: Runner | None = None,
    popen: Popen | None = None,
    patterns: Sequence[Pattern] | None = None,
) -> tuple[Registry, tuple[Pattern, ...]]:
    """Register core + procs verbs and return ``(registry, patterns)``.

    ``session.undo`` is registered by the assembly layer (controller/CLI) via
    ``policy.undo.register_undo`` so L3 never imports L4.
    """
    _ = resolve_site_fn  # reserved for future site-slot resolvers
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
        get_system=get_system,
        get_delivery=get_delivery,
        get_platform=get_platform,
        quit_app_fn=quit_app_fn,
        open_private_fn=open_private_fn,
        reveal_fn=reveal_fn,
        open_dir_fn=open_dir_fn,
        resolve_folder_fn=resolve_folder_fn,
        runner=runner,
        popen=popen,
    ):
        registry.register(verb)
    procs = register_procs_pack(
        registry,
        get_system=get_system,
        get_platform=get_platform,
    )
    base = tuple(patterns) if patterns is not None else core_patterns()
    return registry, base + procs
