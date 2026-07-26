"""Per-OS known-folder resolution for ``files.open_dir``.

Uses OS folder APIs / ``xdg-user-dir`` / Known Folder IDs — never string-builds
``~/Downloads`` style paths.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from vaani.platform.protocol import PlatformId

# Spoken / slot aliases → canonical folder key.
FOLDER_ALIASES: Mapping[str, str] = {
    "downloads": "downloads",
    "download": "downloads",
    "desktop": "desktop",
    "documents": "documents",
    "document": "documents",
    "docs": "documents",
    "pictures": "pictures",
    "photos": "pictures",
    "music": "music",
    "movies": "movies",
    "videos": "movies",
    "home": "home",
    "home folder": "home",
}

_XDG_NAMES: Mapping[str, str] = {
    "downloads": "DOWNLOAD",
    "desktop": "DESKTOP",
    "documents": "DOCUMENTS",
    "pictures": "PICTURES",
    "music": "MUSIC",
    "movies": "VIDEOS",
}

_XDG_ENV: Mapping[str, str] = {
    "downloads": "XDG_DOWNLOAD_DIR",
    "desktop": "XDG_DESKTOP_DIR",
    "documents": "XDG_DOCUMENTS_DIR",
    "pictures": "XDG_PICTURES_DIR",
    "music": "XDG_MUSIC_DIR",
    "movies": "XDG_VIDEOS_DIR",
}

# macOS Finder folder names for `path to … folder`.
_MAC_NAMES: Mapping[str, str] = {
    "downloads": "downloads",
    "desktop": "desktop",
    "documents": "documents",
    "pictures": "pictures",
    "music": "music",
    "movies": "movies",
}

# Windows FOLDERID_* GUIDs (Known Folder IDs).
_WIN_FOLDER_IDS: Mapping[str, str] = {
    "downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
    "desktop": "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}",
    "documents": "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}",
    "pictures": "{33E28130-4E1E-4676-835A-98395C3BC3BB}",
    "music": "{4BD8D571-6D19-48D3-BE97-422220080E43}",
    "movies": "{18989B1D-99B5-455B-841C-AB7C74E4DDFC}",
    "home": "{5E6C858F-0E22-4760-9AFE-EA3317B67173}",
}

Runner = Callable[..., Any]
Getenv = Callable[[str], str | None]
Whicher = Callable[[str], str | None]


def canonical_folder(name: str) -> str | None:
    """Map a spoken/slot folder name to a canonical key, or ``None``."""
    key = " ".join(name.casefold().strip().split())
    return FOLDER_ALIASES.get(key)


def resolve_known_folder(
    name: str,
    platform: PlatformId,
    *,
    runner: Runner | None = None,
    getenv: Getenv | None = None,
    which: Whicher | None = None,
    home: Path | None = None,
) -> Path | None:
    """Resolve a known folder for ``platform``.

    Returns ``None`` when the folder key is unknown or the OS cannot resolve it.
    """
    key = canonical_folder(name)
    if key is None:
        return None
    run = runner or subprocess.run
    env = getenv or (lambda k: os.environ.get(k))
    whicher = which or shutil.which
    if platform is PlatformId.MACOS:
        return _resolve_macos(key, runner=run, home=home, getenv=env)
    if platform is PlatformId.WINDOWS:
        return _resolve_windows(key, home=home, getenv=env)
    return _resolve_linux(key, runner=run, getenv=env, which=whicher, home=home)


def _resolve_linux(
    key: str,
    *,
    runner: Runner,
    getenv: Getenv,
    which: Whicher,
    home: Path | None,
) -> Path | None:
    if key == "home":
        return _home(home, getenv)
    xdg = _XDG_NAMES.get(key)
    if xdg is None:
        return None
    if which("xdg-user-dir"):
        try:
            completed = runner(
                ["xdg-user-dir", xdg],
                check=False,
                capture_output=True,
                text=True,
                timeout=5.0,
            )
        except Exception:
            completed = None
        if completed is not None and getattr(completed, "returncode", 1) == 0:
            raw = (getattr(completed, "stdout", None) or "").strip()
            if raw and not raw.startswith("xdg-user-dir:"):
                path = Path(raw)
                if path.is_absolute():
                    return path
    env_key = _XDG_ENV.get(key)
    if env_key:
        value = getenv(env_key)
        if value:
            return Path(value)
    return None


def _resolve_macos(
    key: str,
    *,
    runner: Runner,
    home: Path | None,
    getenv: Getenv,
) -> Path | None:
    if key == "home":
        return _home(home, getenv)
    folder = _MAC_NAMES.get(key)
    if folder is None:
        return None
    script = f"POSIX path of (path to {folder} folder)"
    try:
        completed = runner(
            ["osascript", "-e", script],
            check=False,
            capture_output=True,
            text=True,
            timeout=5.0,
        )
    except Exception:
        return None
    if getattr(completed, "returncode", 1) != 0:
        return None
    raw = (getattr(completed, "stdout", None) or "").strip().rstrip("/")
    if not raw:
        return None
    return Path(raw)


def _resolve_windows(
    key: str,
    *,
    home: Path | None,
    getenv: Getenv,
) -> Path | None:
    folder_id = _WIN_FOLDER_IDS.get(key)
    if folder_id is None:
        return None
    path = _sh_get_known_folder_path(folder_id)
    if path is not None:
        return path
    if key == "home":
        return _home(home, getenv)
    return None


def _sh_get_known_folder_path(folder_id: str) -> Path | None:
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return None

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", wintypes.BYTE * 8),
        ]

    hex_s = folder_id.strip("{}").replace("-", "")
    try:
        guid = GUID(
            int(hex_s[0:8], 16),
            int(hex_s[8:12], 16),
            int(hex_s[12:16], 16),
            (wintypes.BYTE * 8)(
                *[int(hex_s[16 + i * 2 : 18 + i * 2], 16) for i in range(8)]
            ),
        )
    except Exception:
        return None

    try:
        shell32 = ctypes.windll.shell32  # type: ignore[attr-defined]
    except Exception:
        return None

    SHGetKnownFolderPath = shell32.SHGetKnownFolderPath
    SHGetKnownFolderPath.argtypes = [
        ctypes.POINTER(GUID),
        wintypes.DWORD,
        wintypes.HANDLE,
        ctypes.POINTER(ctypes.c_wchar_p),
    ]
    SHGetKnownFolderPath.restype = ctypes.HRESULT
    path_ptr = ctypes.c_wchar_p()
    hr = SHGetKnownFolderPath(ctypes.byref(guid), 0, 0, ctypes.byref(path_ptr))
    if hr != 0 or not path_ptr.value:
        return None
    return Path(path_ptr.value)


def _home(home: Path | None, getenv: Getenv) -> Path | None:
    if home is not None:
        return home
    value = getenv("HOME") or getenv("USERPROFILE")
    if value:
        return Path(value)
    try:
        return Path.home()
    except Exception:
        return None
