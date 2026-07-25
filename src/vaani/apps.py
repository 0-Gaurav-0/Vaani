"""Deterministic voice aliases for installed desktop applications."""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class AppTarget:
    name: str
    executables: tuple[str, ...]
    arguments: tuple[str, ...] = ()


APPS: tuple[tuple[tuple[str, ...], AppTarget], ...] = (
    (("claude desktop", "claude app", "claude"), AppTarget("Claude", ("claude-desktop",))),
    (("terminal", "gnome terminal"), AppTarget("Terminal", ("gnome-terminal",))),
    (("text editor", "gedit"), AppTarget("Text Editor", ("gnome-text-editor", "gedit"))),
    (("visual studio code", "vs code", "code editor"), AppTarget("Visual Studio Code", ("code",))),
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
    (("cursor editor", "cursor"), AppTarget("Cursor", ("cursor",))),
    (("antigravity ide", "antigravity"), AppTarget("Antigravity", ("antigravity-ide",))),
    (("thunderbird",), AppTarget("Thunderbird", ("thunderbird",))),
    (("remote desktop", "remmina"), AppTarget("Remmina", ("remmina",))),
    (("image viewer",), AppTarget("Image Viewer", ("eog",))),
    (("document viewer", "pdf viewer"), AppTarget("Document Viewer", ("evince",))),
    (("archive manager",), AppTarget("Archive Manager", ("file-roller",))),
    (("gnome tweaks", "tweaks"), AppTarget("Tweaks", ("gnome-tweaks",))),
)


def _contains_phrase(text: str, phrase: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text) is not None


def resolve_app(command: str) -> AppTarget | None:
    normalized = " ".join(command.casefold().strip().split())
    if not re.search(r"\b(open|launch|start|show)\b", normalized):
        return None
    if any(word in normalized for word in (" website", " web app", " in brave", " in chrome", " browser")):
        return None
    for aliases, target in APPS:
        if any(_contains_phrase(normalized, alias) for alias in aliases):
            return target
    return None


def launch_app(target: AppTarget) -> str:
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
