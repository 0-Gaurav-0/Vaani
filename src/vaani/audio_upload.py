"""Prepare captured audio for faster Groq upload (smaller than raw WAV)."""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from pathlib import Path

LOGGER = logging.getLogger("vaani")


def prepare_transcription_upload(wav_path: Path) -> tuple[Path, str, str, bool]:
    """Return ``(path, filename, content_type, is_temp)`` for multipart upload.

    Prefers FLAC (lossless, much smaller for speech). Falls back to the original
    WAV when compression tools are unavailable.
    """
    wav_path = Path(wav_path)
    compressed = _try_soundfile_flac(wav_path)
    if compressed is None and os.name != "nt":
        compressed = _try_afconvert_m4a(wav_path)
    if compressed is None:
        return wav_path, wav_path.name, "audio/wav", False
    path, name, ctype = compressed
    try:
        src = wav_path.stat().st_size
        dst = path.stat().st_size
        LOGGER.info(
            "event=audio_compress src_bytes=%s dst_bytes=%s format=%s",
            src,
            dst,
            ctype,
        )
    except OSError:
        pass
    return path, name, ctype, True


# Edge-silence trim: Whisper hallucinates ("thank you", "subtitles by…") on
# long quiet tails and every second of audio is upload + decode time.
TRIM_PAD_S = 0.3
TRIM_MIN_SAVING_S = 0.5
TRIM_FLOOR = 0.008
TRIM_FACTOR = 3.0


def trim_edge_silence(data, rate: int):
    """Drop leading/trailing silence (keeps ``TRIM_PAD_S`` either side).

    Threshold adapts to the clip's noise floor. Returns ``data`` unchanged
    when there is no clear speech or the saving is small.
    """
    try:
        import numpy as np
    except Exception:
        return data
    try:
        mono = data if data.ndim == 1 else data.mean(axis=1)
        frame = max(1, int(rate * 0.02))
        frames = len(mono) // frame
        if frames < 10:
            return data
        arr = mono[: frames * frame].astype(np.float32).reshape(frames, frame)
        rms = np.sqrt((arr * arr).mean(axis=1)) / 32768.0
        floor = float(np.percentile(rms, 10))
        threshold = max(TRIM_FLOOR, floor * TRIM_FACTOR)
        loud = np.nonzero(rms > threshold)[0]
        if loud.size == 0:
            return data
        pad = int(TRIM_PAD_S / 0.02)
        first = max(0, int(loud[0]) - pad)
        last = min(frames, int(loud[-1]) + 1 + pad)
        saved_s = (frames - (last - first)) * 0.02
        if saved_s < TRIM_MIN_SAVING_S:
            return data
        end = len(mono) if last >= frames else last * frame
        LOGGER.info("event=audio_trim saved_s=%.2f kept_s=%.2f", saved_s, (end - first * frame) / rate)
        return data[first * frame : end]
    except Exception as exc:
        LOGGER.debug("event=audio_trim_failed detail=%s", type(exc).__name__)
        return data


def _try_soundfile_flac(wav_path: Path) -> tuple[Path, str, str] | None:
    try:
        import soundfile as sf
    except Exception:
        return None
    try:
        data, rate = sf.read(str(wav_path), dtype="int16")
        data = trim_edge_silence(data, rate)
        fd, out = tempfile.mkstemp(prefix="vaani-", suffix=".flac")
        os.close(fd)
        out_path = Path(out)
        sf.write(str(out_path), data, rate, format="FLAC")
        return out_path, out_path.name, "audio/flac"
    except Exception as exc:
        LOGGER.debug("event=audio_compress_soundfile_failed detail=%s", type(exc).__name__)
        return None


def _try_afconvert_m4a(wav_path: Path) -> tuple[Path, str, str] | None:
    """macOS built-in compressor — no extra Python deps."""
    afconvert = "/usr/bin/afconvert"
    if not Path(afconvert).is_file():
        return None
    fd, out = tempfile.mkstemp(prefix="vaani-", suffix=".m4a")
    os.close(fd)
    out_path = Path(out)
    try:
        result = subprocess.run(
            [afconvert, "-f", "m4af", "-d", "aac", str(wav_path), str(out_path)],
            check=False,
            capture_output=True,
            timeout=120,
        )
        if result.returncode != 0 or out_path.stat().st_size < 32:
            out_path.unlink(missing_ok=True)
            return None
        return out_path, out_path.name, "audio/mp4"
    except Exception as exc:
        LOGGER.debug("event=audio_compress_afconvert_failed detail=%s", type(exc).__name__)
        try:
            out_path.unlink(missing_ok=True)
        except OSError:
            pass
        return None
