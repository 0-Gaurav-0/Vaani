"""Index installed .desktop apps for voice open (Linux)."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True)
class DesktopApp:
    name: str
    desktop_id: str
    exec_argv: tuple[str, ...]
    aliases: tuple[str, ...]


def _desktop_dirs() -> tuple[Path, ...]:
    home = Path.home()
    data_home = Path(os.environ.get("XDG_DATA_HOME") or (home / ".local" / "share"))
    dirs = [
        data_home / "applications",
        home / ".local" / "share" / "applications",
        Path("/usr/share/applications"),
        Path("/usr/local/share/applications"),
        Path("/var/lib/snapd/desktop/applications"),
    ]
    # Dedupe while preserving order.
    seen: set[Path] = set()
    out: list[Path] = []
    for path in dirs:
        resolved = path
        if resolved in seen:
            continue
        seen.add(resolved)
        out.append(resolved)
    return tuple(out)


def _parse_desktop(path: Path) -> DesktopApp | None:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    if "[Desktop Entry]" not in text:
        return None
    fields: dict[str, str] = {}
    for line in text.splitlines():
        if not line or line.startswith("#") or line.startswith("["):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        fields.setdefault(key.strip(), value.strip())
    if fields.get("Type", "Application") != "Application":
        return None
    if fields.get("NoDisplay", "").lower() == "true":
        return None
    if fields.get("Hidden", "").lower() == "true":
        return None
    name = fields.get("Name") or ""
    if not name:
        return None
    exec_line = fields.get("Exec") or ""
    if not exec_line:
        return None
    argv = _split_exec(exec_line)
    if not argv:
        return None
    desktop_id = path.name[: -len(".desktop")] if path.name.endswith(".desktop") else path.stem
    aliases = [name.casefold()]
    generic = fields.get("GenericName")
    if generic:
        aliases.append(generic.casefold())
    # Filename without vendor prefix often spoken: "signal-desktop" → "signal desktop"
    aliases.append(desktop_id.replace("_", " ").replace("-", " ").casefold())
    # Unique aliases
    seen: set[str] = set()
    uniq: list[str] = []
    for alias in aliases:
        alias = " ".join(alias.split())
        if alias and alias not in seen:
            seen.add(alias)
            uniq.append(alias)
    return DesktopApp(name=name, desktop_id=desktop_id, exec_argv=tuple(argv), aliases=tuple(uniq))


def _split_exec(exec_line: str) -> list[str]:
    """Split Exec= without a shell; drop field codes like %u %f."""
    tokens: list[str] = []
    buf: list[str] = []
    in_quote: str | None = None
    i = 0
    while i < len(exec_line):
        ch = exec_line[i]
        if in_quote:
            if ch == in_quote:
                in_quote = None
            else:
                buf.append(ch)
            i += 1
            continue
        if ch in "'\"":
            in_quote = ch
            i += 1
            continue
        if ch.isspace():
            if buf:
                tokens.append("".join(buf))
                buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    if buf:
        tokens.append("".join(buf))
    cleaned = [t for t in tokens if not t.startswith("%")]
    return cleaned


def _contains_phrase(text: str, phrase: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text) is not None


@lru_cache(maxsize=1)
def load_desktop_apps() -> tuple[DesktopApp, ...]:
    apps: list[DesktopApp] = []
    seen_ids: set[str] = set()
    for directory in _desktop_dirs():
        if not directory.is_dir():
            continue
        try:
            paths = sorted(directory.glob("*.desktop"))
        except OSError:
            continue
        for path in paths:
            entry = _parse_desktop(path)
            if entry is None or entry.desktop_id in seen_ids:
                continue
            seen_ids.add(entry.desktop_id)
            apps.append(entry)
    return tuple(apps)


def resolve_desktop_app(name: str) -> DesktopApp | None:
    normalized = " ".join((name or "").casefold().strip().split())
    if not normalized:
        return None
    ranked: list[tuple[int, DesktopApp]] = []
    for app in load_desktop_apps():
        for alias in app.aliases:
            if len(alias) < 3:
                continue
            if _contains_phrase(normalized, alias) or normalized == alias:
                ranked.append((len(alias), app))
                break
    if not ranked:
        return None
    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked[0][1]


def launch_desktop_app(app: DesktopApp) -> str:
    gtk = shutil.which("gtk-launch")
    if gtk:
        try:
            subprocess.Popen(
                [gtk, app.desktop_id],
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return f"Opened {app.name}."
        except OSError:
            pass
    if not app.exec_argv:
        return f"Unable to open {app.name}."
    exe = app.exec_argv[0]
    resolved = shutil.which(exe) or (exe if Path(exe).is_file() else None)
    if resolved is None:
        return f"Unable to open {app.name}: application is not installed."
    try:
        subprocess.Popen(
            [resolved, *app.exec_argv[1:]],
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return f"Unable to open {app.name}."
    return f"Opened {app.name}."


def clear_desktop_cache() -> None:
    load_desktop_apps.cache_clear()
