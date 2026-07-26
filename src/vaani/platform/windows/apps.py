"""Windows application catalog for voice open/launch/start/show intents."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from ..protocol import AppTarget

# (aliases, AppTarget)
APPS: tuple[tuple[tuple[str, ...], AppTarget], ...] = (
    (
        ("windows terminal", "terminal", "wt"),
        AppTarget("Windows Terminal", ("wt.exe", "wt"), native_name="WindowsTerminal"),
    ),
    (("command prompt", "cmd"), AppTarget("Command Prompt", ("cmd.exe", "cmd"), native_name="cmd")),
    (("notepad", "text editor"), AppTarget("Notepad", ("notepad.exe", "notepad"), native_name="notepad")),
    (
        ("file explorer", "explorer", "files", "file manager"),
        AppTarget("File Explorer", ("explorer.exe", "explorer"), native_name="explorer"),
    ),
    (("calculator", "calc"), AppTarget("Calculator", ("calc.exe", "calc"), native_name="calc")),
    (
        ("visual studio code", "vs code", "code editor", "code"),
        AppTarget("Visual Studio Code", ("code.cmd", "code.exe", "code"), native_name="Code"),
    ),
    (
        ("cursor editor", "cursor"),
        AppTarget("Cursor", ("cursor.cmd", "cursor.exe", "cursor"), native_name="Cursor"),
    ),
    (("paint",), AppTarget("Paint", ("mspaint.exe", "mspaint"), native_name="mspaint")),
    (
        ("task manager",),
        AppTarget("Task Manager", ("taskmgr.exe", "taskmgr"), native_name="Taskmgr"),
    ),
    (
        ("settings", "system settings"),
        AppTarget("Settings", ("ms-settings:",), native_name="Settings"),
    ),
)


def _contains_phrase(text: str, phrase: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text) is not None


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


def _which(name: str) -> str | None:
    if name.endswith(":") and "://" not in name:
        # URI scheme targets (ms-settings:) are launched via os.startfile.
        return name
    found = shutil.which(name)
    if found:
        return found
    # Common Windows install locations when PATH is incomplete.
    home = Path.home()
    candidates = (
        Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / name,
        Path(os.environ.get("SystemRoot", r"C:\Windows")) / name,
        home / "AppData" / "Local" / "Programs" / "Microsoft VS Code" / name,
        home / "AppData" / "Local" / "Programs" / "cursor" / name,
    )
    for path in candidates:
        if path.is_file():
            return str(path)
    return None


_UNSET: Any = object()


def launch_app(
    target: AppTarget,
    *,
    popen: Callable[..., Any] = subprocess.Popen,
    startfile: Any = _UNSET,
) -> str:
    start: Callable[[str], Any] | None
    if startfile is _UNSET:
        start = getattr(os, "startfile", None)
    else:
        start = startfile
    executable = next((path for name in target.executables if (path := _which(name))), None)
    if executable is None and target.native_name and start is not None:
        try:
            start(target.native_name)
            return f"Opened {target.name}."
        except OSError:
            return f"Unable to open {target.name}: application is not installed."
    if executable is None:
        return f"Unable to open {target.name}: application is not installed."
    try:
        if executable.endswith(":") and start is not None:
            start(executable)
        else:
            popen(
                [executable, *target.arguments],
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    except OSError:
        return f"Unable to open {target.name}."
    return f"Opened {target.name}."


class WindowsAppLauncher:
    def resolve(self, command: str) -> AppTarget | None:
        return resolve_app(command)

    def launch(self, target: AppTarget) -> str:
        return launch_app(target)
