"""Keep each dictation chunk (audio + text) on disk for a short while.

A capped or crashed session used to lose everything said so far: chunk
transcripts lived only in memory and the WAV was unlinked on failure. Each
chunk the :class:`~vaani.stt_chunks.ChunkedTranscriber` cuts is now written to
``<root>/<YYYYmmdd-HHMMSS>/`` as FLAC (lossless, ~2.3x smaller than WAV; ~3 ms
CPU per 20 s chunk) *before* it is uploaded, plus one line in
``transcript.txt`` once the upload settles. A recording left behind by a crash
is moved to ``<root>/<stamp>-recovered/`` at startup instead of being swept,
and one whose processing fails goes to ``<root>/<stamp>-failed/`` right away.
Sessions older than ``RETENTION_S`` are deleted when the next session starts.
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

    def save_audio(self, index: int, pcm: bytes) -> None:
        """Write chunk audio. Called before upload; never raises."""
        try:
            with self._lock:
                _write_audio(self._ensure_dir() / f"chunk-{index:03d}", pcm)
        except Exception as exc:
            self._logger.warning("event=session_archive_failed index=%s detail=%s", index, type(exc).__name__)

    def save_text(self, index: int, text: str | None) -> None:
        """Append the chunk's transcript line. Never raises."""
        try:
            with self._lock:
                path = self._ensure_dir() / "transcript.txt"
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                with os.fdopen(fd, "a", encoding="utf-8") as fh:
                    fh.write(f"[{index:03d}] {text or '<no transcript>'}\n")
        except Exception as exc:
            self._logger.warning("event=session_archive_failed index=%s detail=%s", index, type(exc).__name__)


MIN_RECOVER_BYTES = 44 + RATE * 2  # at least ~1 s of audio


def recover_recordings(audio_dir: Path, root: Path, *, background: bool = True) -> list[Path]:
    """Move leftover ``recording-*.wav`` files (a crashed session) into ``root``.

    Must run before :func:`vaani.config.sweep_audio_directory`, which deletes
    them. The move is a rename; FLAC compression runs once, off-thread.
    """
    moved: list[Path] = []
    if not audio_dir.is_dir() or audio_dir.is_symlink():
        return moved
    for wav_path in sorted(audio_dir.glob("recording-*.wav")):
        try:
            st = wav_path.lstat()
            if wav_path.is_symlink() or not wav_path.is_file() or st.st_size < MIN_RECOVER_BYTES:
                continue
            if hasattr(os, "getuid") and st.st_uid != os.getuid():
                continue
            moved.append(_move_into(wav_path, root, "recovered", st.st_mtime))
        except OSError:
            continue
    if moved:
        _compress_later(moved, background)
    return moved


def preserve_recording(wav_path: Path, root: Path, *, label: str = "failed", background: bool = True) -> Path | None:
    """Move a recording whose processing failed into ``root``. Never raises."""
    try:
        wav_path = Path(wav_path)
        if wav_path.is_symlink() or not wav_path.is_file() or wav_path.stat().st_size < MIN_RECOVER_BYTES:
            return None
        target = _move_into(wav_path, root, label, time.time())
    except OSError as exc:
        LOGGER.warning("event=session_preserve_failed detail=%s", type(exc).__name__)
        return None
    _compress_later([target], background)
    return target


def _move_into(wav_path: Path, root: Path, label: str, mtime: float) -> Path:
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(mtime))
    target_dir = root / f"{stamp}-{label}"
    n = 1
    while target_dir.exists():
        n += 1
        target_dir = root / f"{stamp}-{label}-{n}"
    target_dir.mkdir(mode=0o700)
    target = target_dir / "recording.wav"
    shutil.move(str(wav_path), str(target))
    return target


def _compress_later(paths: list[Path], background: bool) -> None:
    if background:
        threading.Thread(target=_compress_all, args=(paths,), daemon=True, name="vaani-recover").start()
    else:
        _compress_all(paths)


def _compress_all(paths: list[Path]) -> None:
    for wav_path in paths:
        try:
            # parec's header sizes are unreliable after a crash; read raw PCM.
            from .stt_chunks import FRAME_BYTES, _data_offset

            data = wav_path.read_bytes()[_data_offset(wav_path):]
            pcm = data[: len(data) // FRAME_BYTES * FRAME_BYTES]
            out = _write_audio(wav_path.with_suffix(""), pcm)
            if out.suffix != ".wav":
                wav_path.unlink(missing_ok=True)
        except Exception as exc:
            LOGGER.warning("event=session_recover_compress_failed detail=%s", type(exc).__name__)


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
