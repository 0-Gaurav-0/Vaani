from __future__ import annotations

from dataclasses import dataclass
import os
import sys
from pathlib import Path
from typing import Mapping

CONNECT_TIMEOUT = 5.0
UPLOAD_TIMEOUT = 30.0
TRANSCRIPTION_READ_TIMEOUT = 120.0
TRANSCRIPTION_DEADLINE = 150.0
CLEANUP_READ_TIMEOUT = 60.0
CLEANUP_DEADLINE = 75.0
POOL_ACQUISITION_TIMEOUT = 5.0
SQLITE_BUSY_TIMEOUT_MS = 2000
MAX_RECORDING_SECONDS = 600
MIN_RECORDING_SECONDS = 0.250
MAX_AUDIO_BYTES = 25 * 1024 * 1024


def _platform_name(platform: str | None = None) -> str:
    value = (platform if platform is not None else sys.platform).casefold()
    if value.startswith("linux"):
        return "linux"
    if value == "darwin":
        return "macos"
    if value in {"win32", "cygwin", "msys"}:
        return "windows"
    return value


def _roots_for_platform(home: Path, platform: str) -> tuple[Path, Path, Path]:
    if platform == "macos":
        data = home / "Library" / "Application Support" / "Vaani"
        cache = home / "Library" / "Caches" / "Vaani"
        config = home / "Library" / "Application Support" / "Vaani" / "config"
        return data, cache, config
    if platform == "windows":
        data = Path(os.environ.get("APPDATA") or (home / "AppData" / "Roaming")) / "Vaani"
        cache = Path(os.environ.get("LOCALAPPDATA") or (home / "AppData" / "Local")) / "Vaani" / "Cache"
        config = data / "config"
        return data, cache, config
    # Linux / XDG-style default (also used for unknown Unix)
    data = home / ".local" / "share" / "vaani"
    cache = home / ".cache" / "vaani"
    config = home / ".config" / "vaani"
    return data, cache, config


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    cache_dir: Path
    config_dir: Path
    log_dir: Path
    audio_dir: Path
    history_db: Path
    debug: bool = False
    platform: str = "linux"

    @property
    def amplitude_path(self) -> Path:
        return self.cache_dir / "amplitude"

    @property
    def indicator_control_path(self) -> Path:
        return self.cache_dir / "indicator_control.json"

    @classmethod
    def from_home(
        cls,
        home: Path | None = None,
        *,
        debug: bool = False,
        platform: str | None = None,
    ) -> "Settings":
        root = Path(home or Path.home())
        plat = _platform_name(platform)
        data, cache, config = _roots_for_platform(root, plat)
        return cls(
            data,
            cache,
            config,
            data / "logs",
            cache / "audio",
            data / "history.sqlite3",
            debug,
            plat,
        )

    def prepare(self) -> None:
        if os.name == "nt":
            for directory in (self.data_dir, self.cache_dir, self.config_dir, self.log_dir, self.audio_dir):
                directory.mkdir(parents=True, exist_ok=True)
            return
        old = os.umask(0o077)
        try:
            for directory in (self.data_dir, self.cache_dir, self.config_dir, self.log_dir, self.audio_dir):
                directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                try:
                    directory.chmod(0o700)
                except OSError:
                    # macOS Application Support / TCC can refuse chmod; mkdir is enough.
                    pass
            if self.history_db.exists():
                try:
                    self.history_db.chmod(0o600)
                except OSError:
                    pass
        finally:
            os.umask(old)


def child_environment(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    source = os.environ if environ is None else environ
    allowed = {
        "HOME",
        "PATH",
        "LANG",
        "LANGUAGE",
        "LC_ALL",
        "LC_CTYPE",
        "DISPLAY",
        "XAUTHORITY",
        "XDG_RUNTIME_DIR",
        "PULSE_SERVER",
        "VAANI_AMPLITUDE_PATH",
        "VAANI_INDICATOR_CONTROL",
    }
    return {k: v for k, v in source.items() if k in allowed}


def sweep_audio_directory(audio_dir: Path, *, uid: int | None = None) -> list[Path]:
    removed: list[Path] = []
    if not audio_dir.is_dir() or audio_dir.is_symlink():
        return removed
    owner = None
    if hasattr(os, "getuid"):
        owner = os.getuid() if uid is None else uid
    for entry in audio_dir.iterdir():
        try:
            stat = entry.lstat()
            if entry.is_symlink() or not entry.is_file():
                continue
            if owner is not None and getattr(stat, "st_uid", owner) != owner:
                continue
            if hasattr(os, "chmod"):
                try:
                    entry.chmod(0o600)
                except OSError:
                    pass
            entry.unlink()
            removed.append(entry)
        except (FileNotFoundError, PermissionError):
            continue
    return removed
