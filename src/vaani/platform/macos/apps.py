"""macOS app catalog launched with ``open -a``."""
from __future__ import annotations

import re
import subprocess
from typing import Any, Callable

from ..protocol import AppTarget

# (aliases, AppTarget) — native_name is the macOS application name for `open -a`.
APPS: tuple[tuple[tuple[str, ...], AppTarget], ...] = (
    (
        ("terminal",),
        AppTarget("Terminal", ("Terminal",), native_name="Terminal"),
    ),
    (
        ("finder", "file manager", "files"),
        AppTarget("Finder", ("Finder",), native_name="Finder"),
    ),
    (
        ("calculator",),
        AppTarget("Calculator", ("Calculator",), native_name="Calculator"),
    ),
    (
        ("system settings", "settings", "system preferences"),
        AppTarget("System Settings", ("System Settings",), native_name="System Settings"),
    ),
    (
        ("textedit", "text edit", "text editor"),
        AppTarget("TextEdit", ("TextEdit",), native_name="TextEdit"),
    ),
    (
        ("visual studio code", "vs code", "code editor"),
        AppTarget("Visual Studio Code", ("Visual Studio Code",), native_name="Visual Studio Code"),
    ),
    (
        ("cursor editor", "cursor"),
        AppTarget("Cursor", ("Cursor",), native_name="Cursor"),
    ),
    (
        ("safari",),
        AppTarget("Safari", ("Safari",), native_name="Safari"),
    ),
    (
        ("chrome", "google chrome"),
        AppTarget("Google Chrome", ("Google Chrome",), native_name="Google Chrome"),
    ),
    (
        ("brave", "brave browser"),
        AppTarget("Brave Browser", ("Brave Browser",), native_name="Brave Browser"),
    ),
    (
        ("notes",),
        AppTarget("Notes", ("Notes",), native_name="Notes"),
    ),
    (
        ("preview",),
        AppTarget("Preview", ("Preview",), native_name="Preview"),
    ),
    (
        ("activity monitor", "task manager", "system monitor"),
        AppTarget("Activity Monitor", ("Activity Monitor",), native_name="Activity Monitor"),
    ),
    (
        ("mail",),
        AppTarget("Mail", ("Mail",), native_name="Mail"),
    ),
    (
        ("calendar",),
        AppTarget("Calendar", ("Calendar",), native_name="Calendar"),
    ),
    (
        ("messages",),
        AppTarget("Messages", ("Messages",), native_name="Messages"),
    ),
)


def _contains_phrase(text: str, phrase: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text) is not None


def lookup_app(name: str) -> AppTarget | None:
    """Resolve a bare app name/alias (no open/launch verb required)."""
    needle = " ".join(name.casefold().strip().split())
    if not needle:
        return None
    for aliases, target in APPS:
        if target.name.casefold() == needle:
            return target
        if target.native_name and target.native_name.casefold() == needle:
            return target
        if any(alias.casefold() == needle for alias in aliases):
            return target
    return None


def resolve_app(command: str) -> AppTarget | None:
    normalized = " ".join(command.casefold().strip().split())
    if not re.search(r"\b(open|launch|start|show)\b", normalized):
        return None
    if any(
        word in normalized
        for word in (" website", " web app", " in brave", " in chrome", " browser")
    ):
        return None
    for aliases, target in APPS:
        if any(_contains_phrase(normalized, alias) for alias in aliases):
            return target
    return None


def launch_app(
    target: AppTarget,
    *,
    runner: Callable[..., Any] | None = None,
) -> str:
    app_name = target.native_name or target.name
    run = runner or subprocess.run
    try:
        result = run(
            ["open", "-a", app_name],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return f"Unable to open {target.name}."
    if getattr(result, "returncode", 1) != 0:
        return f"Unable to open {target.name}: application is not installed."
    return f"Opened {target.name}."


class MacAppLauncher:
    def __init__(self, *, runner: Callable[..., Any] | None = None):
        self._runner = runner

    def resolve(self, command: str) -> AppTarget | None:
        return resolve_app(command)

    def launch(self, target: AppTarget) -> str:
        return launch_app(target, runner=self._runner)
