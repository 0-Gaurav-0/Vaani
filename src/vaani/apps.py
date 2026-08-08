"""Deterministic voice aliases for installed desktop applications."""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .xdg_apps import launch_desktop_app, resolve_desktop_app

LOGGER = logging.getLogger("vaani")


@dataclass(frozen=True)
class AppTarget:
    name: str
    executables: tuple[str, ...]
    arguments: tuple[str, ...] = ()
    desktop_id: str | None = None


APPS: tuple[tuple[tuple[str, ...], AppTarget], ...] = (
    (("claude desktop", "claude app", "claude"), AppTarget("Claude", ("claude-desktop",))),
    (("terminal", "gnome terminal"), AppTarget("Terminal", ("gnome-terminal",))),
    (("text editor", "gedit"), AppTarget("Text Editor", ("gnome-text-editor", "gedit"))),
    (("visual studio code", "vs code", "vscode", "vs-code", "code editor", "code"), AppTarget("Visual Studio Code", ("code",))),
    (("file manager", "files"), AppTarget("Files", ("nautilus",))),
    (("calculator",), AppTarget("Calculator", ("gnome-calculator",))),
    (("system settings", "settings"), AppTarget("Settings", ("gnome-control-center",))),
    (("calendar",), AppTarget("Calendar", ("gnome-calendar",))),
    (("system monitor", "task manager"), AppTarget("System Monitor", ("gnome-system-monitor",))),
    (("dbeaver", "db beaver"), AppTarget("DBeaver", ("dbeaver", "dbeaver-ce"))),
    (("mongodb compass", "mongo compass"), AppTarget("MongoDB Compass", ("mongodb-compass",))),
    (("libreoffice writer", "libre office writer", "writer"), AppTarget("LibreOffice Writer", ("libreoffice",), ("--writer",))),
    (("libreoffice calc", "libre office calc"), AppTarget("LibreOffice Calc", ("libreoffice",), ("--calc",))),
    (("libreoffice impress", "libre office impress"), AppTarget("LibreOffice Impress", ("libreoffice",), ("--impress",))),
    (("libreoffice", "libre office"), AppTarget("LibreOffice", ("libreoffice",))),
    # Quick resume: this machine's Cursor workspace (~/Vaani → control repo).
    (
        (
            "vaani project",
            "vani project",
            "wani project",
            "project vaani",
            "project vani",
            "project wani",
            "vaani session",
            "vani session",
        ),
        AppTarget(
            "Vaani project",
            ("cursor",),
            (str(Path.home() / "Vaani"),),
        ),
    ),
    (("cursor editor", "cursor"), AppTarget("Cursor", ("cursor",))),
    (("antigravity ide", "antigravity"), AppTarget("Antigravity", ("antigravity-ide",))),
    (("thunderbird",), AppTarget("Thunderbird", ("thunderbird",))),
    (("remote desktop", "remmina"), AppTarget("Remmina", ("remmina",))),
    (("image viewer",), AppTarget("Image Viewer", ("eog",))),
    (("document viewer", "pdf viewer"), AppTarget("Document Viewer", ("evince",))),
    (("archive manager",), AppTarget("Archive Manager", ("file-roller",))),
    (("gnome tweaks", "tweaks"), AppTarget("Tweaks", ("gnome-tweaks",))),
    (
        ("google chrome", "chrome"),
        AppTarget(
            "Chrome",
            ("google-chrome", "google-chrome-stable", "chromium-browser", "chromium"),
        ),
    ),
    (("brave browser", "brave"), AppTarget("Brave", ("brave-browser", "brave"))),
    (("firefox",), AppTarget("Firefox", ("firefox",))),
    (("spotify",), AppTarget("Spotify", ("spotify",))),
    (("slack",), AppTarget("Slack", ("slack",))),
    (("discord",), AppTarget("Discord", ("discord",))),
    (("obsidian",), AppTarget("Obsidian", ("obsidian",))),
)

_OPEN_APP_RE = re.compile(
    r"\b("
    r"open|launch|start|show|visit|initiate|resume|"
    r"kholo|khol|dikhao|dikha|chalu\s*karo|shuru\s*karo"
    r")\b"
)
_WEB_HINT_RE = re.compile(
    r"\b(website|web\s*app|in\s+brave|in\s+chrome|browser|site)\b"
)


def _contains_phrase(text: str, phrase: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text) is not None


def load_user_apps(path: Path | None = None) -> tuple[tuple[tuple[str, ...], AppTarget], ...]:
    """Load ~/.config/vaani/apps.json; invalid files fail closed."""
    config = path or Path.home() / ".config" / "vaani" / "apps.json"
    if not config.is_file():
        return ()
    try:
        data = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        LOGGER.warning("event=apps_json_ignored detail=%s", type(exc).__name__)
        return ()
    if not isinstance(data, dict):
        return ()
    entries = data.get("apps")
    if not isinstance(entries, list):
        return ()
    loaded: list[tuple[tuple[str, ...], AppTarget]] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        aliases = item.get("aliases")
        executables = item.get("executables")
        if not isinstance(name, str) or not name.strip():
            continue
        if not isinstance(aliases, list) or not aliases:
            continue
        if not isinstance(executables, list) or not executables:
            continue
        alias_tuple = tuple(
            " ".join(str(a).casefold().split()) for a in aliases if str(a).strip()
        )
        exe_tuple = tuple(str(e) for e in executables if str(e).strip())
        if not alias_tuple or not exe_tuple:
            continue
        args = item.get("arguments") or []
        arg_tuple = tuple(str(a) for a in args) if isinstance(args, list) else ()
        desktop_id = item.get("desktop_id")
        desktop = str(desktop_id) if isinstance(desktop_id, str) and desktop_id.strip() else None
        loaded.append(
            (
                alias_tuple,
                AppTarget(name.strip(), exe_tuple, arg_tuple, desktop),
            )
        )
    return tuple(loaded)


def _catalog() -> tuple[tuple[tuple[str, ...], AppTarget], ...]:
    return load_user_apps() + APPS


def resolve_app_name(name: str) -> AppTarget | None:
    """Match an app by spoken name alone (no open/launch verb required)."""
    normalized = " ".join((name or "").casefold().strip().split())
    if not normalized:
        return None
    # Longest alias first so "claude desktop" beats "claude".
    ranked: list[tuple[int, AppTarget]] = []
    for aliases, target in _catalog():
        for alias in aliases:
            if _contains_phrase(normalized, alias) or normalized == alias:
                ranked.append((len(alias), target))
                break
    if ranked:
        ranked.sort(key=lambda item: item[0], reverse=True)
        return ranked[0][1]
    desktop = resolve_desktop_app(normalized)
    if desktop is None:
        return None
    return AppTarget(
        desktop.name,
        (desktop.exec_argv[0],) if desktop.exec_argv else (),
        tuple(desktop.exec_argv[1:]) if len(desktop.exec_argv) > 1 else (),
        desktop.desktop_id,
    )


def resolve_app(command: str) -> AppTarget | None:
    normalized = " ".join(command.casefold().strip().split())
    if not _OPEN_APP_RE.search(normalized):
        return None
    if _WEB_HINT_RE.search(normalized):
        return None
    return resolve_app_name(normalized)


def launch_app(target: AppTarget) -> str:
    if target.desktop_id:
        from .xdg_apps import DesktopApp

        return launch_desktop_app(
            DesktopApp(
                name=target.name,
                desktop_id=target.desktop_id,
                exec_argv=(target.executables[0], *target.arguments)
                if target.executables
                else (),
                aliases=(),
            )
        )
    executable = next((path for name in target.executables if (path := shutil.which(name))), None)
    if executable is None:
        return f"Unable to open {target.name}: application is not installed."
    try:
        subprocess.Popen(
            [executable, *target.arguments],
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return f"Unable to open {target.name}."
    return f"Opened {target.name}."
