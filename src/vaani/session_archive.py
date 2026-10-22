"""Keep each dictation chunk (audio + text) on disk for a short while.

A capped or crashed session used to lose everything said so far: chunk
transcripts lived only in memory and the WAV was unlinked on failure. Each
chunk the :class:`~vaani.stt_chunks.ChunkedTranscriber` cuts is now written to
``<root>/<YYYYmmdd-HHMMSS>/`` as FLAC (lossless, ~2.3x smaller than WAV; ~3 ms
CPU per 20 s chunk) plus one line in ``transcript.txt``. Sessions older than
``RETENTION_S`` are deleted when the next session starts.
"""
from __future__ import annotations

import logging
import os
import shutil
import threading
import time
import wave
from pathlib import Path

LOGGER = logging.getLogger("vaani")

RETENTION_S = 48 * 3600
RATE = 16000


def sweep_sessions(root: Path, *, max_age_s: float = RETENTION_S, now: float | None = None) -> int:
    """Delete session directories last written more than ``max_age_s`` ago."""
    if not root.is_dir() or root.is_symlink():
        return 0
    cutoff = (time.time() if now is None else now) - max_age_s
    removed = 0
    for entry in root.iterdir():
        try:
            if entry.is_symlink() or not entry.is_dir() or entry.stat().st_mtime >= cutoff:
                continue
            shutil.rmtree(entry)
            removed += 1
        except OSError:
            continue
    return removed


class SessionArchive:
    def __init__(self, root: Path | str, *, logger: logging.Logger = LOGGER):
        self.root = Path(root)
        self._logger = logger
        self._dir: Path | None = None
        self._lock = threading.Lock()
        try:
            removed = sweep_sessions(self.root)
            if removed:
                self._logger.info("event=session_archive_swept removed=%s", removed)
        except Exception as exc:
            self._logger.debug("event=session_archive_sweep_failed detail=%s", type(exc).__name__)

    @property
    def directory(self) -> Path | None:
        return self._dir

    def _ensure_dir(self) -> Path:
        if self._dir is None:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            path = self.root / stamp
            n = 1
            while path.exists():
                n += 1
                path = self.root / f"{stamp}-{n}"
            path.mkdir(mode=0o700)
            self._dir = path
        return self._dir

    def save_chunk(self, index: int, pcm: bytes, text: str | None) -> None:
        """Write chunk audio and its transcript line. Never raises."""
        try:
            with self._lock:
                directory = self._ensure_dir()
                _write_audio(directory / f"chunk-{index:03d}", pcm)
                line = text if text else "<no transcript>"
                with open(directory / "transcript.txt", "a", encoding="utf-8") as fh:
                    fh.write(f"[{index:03d}] {line}\n")
        except Exception as exc:
            self._logger.warning("event=session_archive_failed index=%s detail=%s", index, type(exc).__name__)


def _write_audio(stem: Path, pcm: bytes) -> Path:
    try:
        import numpy as np
        import soundfile as sf
    except Exception:
        np = sf = None
    if sf is not None:
        out = stem.with_suffix(".flac")
        sf.write(str(out), np.frombuffer(pcm, dtype="<i2"), RATE, format="FLAC")
    else:
        out = stem.with_suffix(".wav")
        with wave.open(str(out), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(RATE)
            wav.writeframes(pcm)
    os.chmod(out, 0o600)
    return out
