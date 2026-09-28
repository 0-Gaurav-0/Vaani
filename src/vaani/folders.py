"""Voice aliases for common user folders (xdg-open)."""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FolderTarget:
    name: str
    path: Path


_OPEN_RE = re.compile(
    r"\b("
    r"open|launch|show|visit|"
    r"kholo|khol|dikhao|dikha"
    r")\b"
)


def _user_dirs() -> dict[str, Path]:
    home = Path.home()
    return {
        "home": home,
        "downloads": home / "Downloads",
        "documents": home / "Documents",
        "desktop": home / "Desktop",
        "pictures": home / "Pictures",
        "music": home / "Music",
        "videos": home / "Videos",
        "trash": Path(os.environ.get("XDG_DATA_HOME") or (home / ".local" / "share"))
        / "Trash"
        / "files",
    }


# Longer aliases first via resolve ranking.
_FOLDER_ALIASES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("downloads folder", "download folder", "downloads", "download"), "downloads"),
    (("documents folder", "document folder", "documents", "docs folder"), "documents"),
    (("pictures folder", "picture folder", "pictures", "photos"), "pictures"),
    (("music folder", "music"), "music"),
    (("videos folder", "video folder", "videos"), "videos"),
    (("desktop folder", "desktop"), "desktop"),
    (("trash folder", "recycle bin", "trash"), "trash"),
    (("home folder", "home directory", "my home", "home"), "home"),
)


def _contains_phrase(text: str, phrase: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text) is not None


def resolve_folder_name(name: str) -> FolderTarget | None:
    normalized = " ".join((name or "").casefold().strip().split())
    if not normalized:
        return None
    ranked: list[tuple[int, str]] = []
    for aliases, key in _FOLDER_ALIASES:
        for alias in aliases:
            if _contains_phrase(normalized, alias) or normalized == alias:
                ranked.append((len(alias), key))
                break
    if not ranked:
        return None
    ranked.sort(key=lambda item: item[0], reverse=True)
    key = ranked[0][1]
    path = _user_dirs()[key]
    label = {
        "home": "Home",
        "downloads": "Downloads",
        "documents": "Documents",
        "desktop": "Desktop",
        "pictures": "Pictures",
        "music": "Music",
        "videos": "Videos",
        "trash": "Trash",
    }[key]
    return FolderTarget(label, path)


def resolve_folder(command: str) -> FolderTarget | None:
    normalized = " ".join(command.casefold().strip().split())
    if not _OPEN_RE.search(normalized):
        # Allow bare "downloads folder" style without verb when clearly a folder ask.
        if not re.search(r"\bfolder\b|\bdirectory\b", normalized):
            return None
    return resolve_folder_name(normalized)


def launch_folder(target: FolderTarget) -> str:
    path = target.path
    if not path.exists():
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError:
            return f"Unable to open {target.name}: folder not found."
    opener = "xdg-open"
    try:
        subprocess.Popen(
            [opener, str(path)],
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return f"Unable to open {target.name}."
    return f"Opened {target.name}."
