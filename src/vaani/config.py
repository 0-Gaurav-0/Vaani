from dataclasses import dataclass
import os
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

@dataclass(frozen=True)
class Settings:
    data_dir: Path
    cache_dir: Path
    config_dir: Path
    log_dir: Path
    audio_dir: Path
    history_db: Path
    debug: bool = False

    @classmethod
    def from_home(cls, home: Path | None = None, *, debug: bool = False) -> "Settings":
        root = Path(home or Path.home())
        data = root / ".local" / "share" / "vaani"
        cache = root / ".cache" / "vaani"
        config = root / ".config" / "vaani"
        return cls(data, cache, config, data / "logs", cache / "audio", data / "history.sqlite3", debug)

    def prepare(self) -> None:
        old = os.umask(0o077)
        try:
            for directory in (self.data_dir, self.cache_dir, self.config_dir, self.log_dir, self.audio_dir):
                directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                directory.chmod(0o700)
            if self.history_db.exists():
                self.history_db.chmod(0o600)
        finally:
            os.umask(old)

def child_environment(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    source = os.environ if environ is None else environ
    allowed = {"HOME", "PATH", "LANG", "LANGUAGE", "LC_ALL", "LC_CTYPE", "DISPLAY", "XAUTHORITY", "XDG_RUNTIME_DIR", "PULSE_SERVER"}
    return {k: v for k, v in source.items() if k in allowed}

def sweep_audio_directory(audio_dir: Path, *, uid: int | None = None) -> list[Path]:
    owner = os.getuid() if uid is None else uid
    removed: list[Path] = []
    if not audio_dir.is_dir() or audio_dir.is_symlink():
        return removed
    for entry in audio_dir.iterdir():
        try:
            stat = entry.lstat()
            if entry.is_symlink() or not entry.is_file() or stat.st_uid != owner:
                continue
            entry.chmod(0o600)
            entry.unlink()
            removed.append(entry)
        except (FileNotFoundError, PermissionError):
            continue
    return removed
