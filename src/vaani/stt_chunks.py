"""Transcribe long dictation in packets while the user is still speaking.

Groq Whisper is a batch upload API (no streaming), so a 90s clip used to wait
for one ~2.7s request after release. :class:`ChunkedTranscriber` watches the
growing parec WAV, cuts at natural pauses once a chunk reaches
``min_chunk_s``, and transcribes each packet in the background. After release
only the short tail is uploaded, so wait time stays near the short-clip floor.

Clips shorter than ``min_chunk_s`` never cut and fall through to the normal
single-request path unchanged (``finish`` returns None). Any chunk failure
also returns None so the caller transcribes the whole file as before.
"""
from __future__ import annotations

import logging
import math
import os
import struct
import tempfile
import threading
import time
import wave
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Callable

LOGGER = logging.getLogger("vaani")

RATE = 16000
BYTES_PER_S = RATE * 2  # s16le mono
FRAME_BYTES = 640  # 20ms
MIN_CHUNK_S = 20.0
MAX_CHUNK_S = 55.0
PAUSE_S = 0.45
MIN_TAIL_S = 0.4
POLL_S = 0.25
# Pause detection: frame RMS below max(floor, factor * quiet-percentile).
SILENCE_FLOOR = 0.006
SILENCE_FACTOR = 1.8


def chunked_stt_enabled() -> bool:
    return os.environ.get("VAANI_CHUNKED_STT", "1").strip().lower() not in {"0", "false", "no", "off"}


def _frame_rms(pcm: bytes) -> list[float]:
    frames = len(pcm) // FRAME_BYTES
    if frames <= 0:
        return []
    try:
        import numpy as np  # linux extra; ~50x faster than the loop below
    except ImportError:
        np = None
    if np is not None:
        arr = np.frombuffer(pcm[: frames * FRAME_BYTES], dtype="<i2").astype(np.float32)
        arr = arr.reshape(frames, FRAME_BYTES // 2)
        return (np.sqrt((arr * arr).mean(axis=1)) / 32768.0).tolist()
    out: list[float] = []
    for i in range(0, len(pcm) - FRAME_BYTES + 1, FRAME_BYTES):
        vals = struct.unpack("<%dh" % (FRAME_BYTES // 2), pcm[i : i + FRAME_BYTES])
        out.append(math.sqrt(sum(v * v for v in vals) / len(vals)) / 32768.0)
    return out


def _data_offset(path: Path) -> int:
    """Byte offset of PCM in a (possibly still-growing) WAV."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(512)
    except OSError:
        return 44
    idx = head.find(b"data")
    return idx + 8 if idx >= 12 else 44


SPEECH_MIN_S = 0.2  # this much audio above the threshold counts as speech
# Same floor the whole-clip silence gate uses (audio.SILENCE_PEAK_RMS); speech
# that passes that gate passes this. Relative part is capped so steady,
# pause-free speech is never mistaken for noise.
SPEECH_FLOOR = 0.012


def has_speech(pcm: bytes) -> bool:
    """Conservative: False only when the audio is clearly just silence/noise."""
    rms = _frame_rms(pcm)
    if not rms:
        return False
    quiet = sorted(rms)[max(0, len(rms) // 10)]
    threshold = max(SPEECH_FLOOR, min(quiet * 3.0, 0.03))
    loud = sum(1 for r in rms if r > threshold)
    return loud * 0.02 >= SPEECH_MIN_S


def find_cut(pcm: bytes, *, min_bytes: int, max_bytes: int, pause_s: float = PAUSE_S) -> int | None:
    """Byte offset (frame-aligned) of a pause to cut at, or None to keep going.

    Looks for ``pause_s`` of quiet after ``min_bytes``; past ``max_bytes`` with
    no pause, cuts at the quietest frame of the last 5s so chunks stay bounded.
    """
    if len(pcm) < min_bytes:
        return None
    rms = _frame_rms(pcm)
    if not rms:
        return None
    quiet = sorted(rms)[max(0, len(rms) // 10)]
    threshold = max(SILENCE_FLOOR, quiet * SILENCE_FACTOR)
    need = max(1, int(pause_s * 1000 / 20))
    start = min_bytes // FRAME_BYTES
    run = 0
    for i in range(start, len(rms)):
        if rms[i] < threshold:
            run += 1
            if run >= need:
                mid = i - run // 2
                return mid * FRAME_BYTES
        else:
            run = 0
    if len(pcm) >= max_bytes:
        lo = max(start, len(rms) - 250)
        best = min(range(lo, len(rms)), key=lambda k: rms[k])
        return best * FRAME_BYTES
    return None


def write_wav(pcm: bytes, directory: Path) -> Path:
    fd, name = tempfile.mkstemp(prefix="chunk-", suffix=".wav", dir=directory)
    os.close(fd)
    path = Path(name)
    os.chmod(path, 0o600)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes(pcm)
    return path


class ChunkedTranscriber:
    def __init__(
        self,
        wav_path: Path | str,
        transcribe: Callable[[Path], str],
        *,
        min_chunk_s: float = MIN_CHUNK_S,
        max_chunk_s: float = MAX_CHUNK_S,
        poll_s: float = POLL_S,
        logger: logging.Logger = LOGGER,
    ):
        self.path = Path(wav_path)
        self._transcribe = transcribe
        self.min_bytes = int(min_chunk_s * BYTES_PER_S) // FRAME_BYTES * FRAME_BYTES
        self.max_bytes = int(max_chunk_s * BYTES_PER_S) // FRAME_BYTES * FRAME_BYTES
        self.poll_s = poll_s
        self._logger = logger
        self._stop = threading.Event()
        self._cut = 0  # PCM bytes already handed to chunks
        self._futures: list[Future] = []
        # One worker keeps packets ordered and under Groq RPM.
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vaani-stt-chunk")
        self._thread: threading.Thread | None = None
        self._failed = False

    @property
    def chunks(self) -> int:
        return len(self._futures)

    def start(self) -> "ChunkedTranscriber":
        self._thread = threading.Thread(target=self._watch, daemon=True, name="vaani-stt-watch")
        self._thread.start()
        return self

    def _read_pcm(self) -> bytes:
        offset = _data_offset(self.path)
        try:
            with open(self.path, "rb") as fh:
                fh.seek(offset + self._cut)
                data = fh.read()
        except OSError:
            return b""
        return data[: len(data) // FRAME_BYTES * FRAME_BYTES]

    def _submit(self, pcm: bytes) -> Future:
        index = len(self._futures)

        def job() -> str:
            chunk = write_wav(pcm, self.path.parent)
            started = time.monotonic()
            try:
                text = (self._transcribe(chunk) or "").strip()
            finally:
                try:
                    chunk.unlink(missing_ok=True)
                except OSError:
                    pass
            self._logger.info(
                "event=stt_chunk_done index=%s seconds=%.1f chars=%s elapsed=%.2f",
                index,
                len(pcm) / BYTES_PER_S,
                len(text),
                time.monotonic() - started,
            )
            return text

        future = self._pool.submit(job)
        self._futures.append(future)
        return future

    def _watch(self) -> None:
        while not self._stop.wait(self.poll_s):
            try:
                pcm = self._read_pcm()
                cut = find_cut(pcm, min_bytes=self.min_bytes, max_bytes=self.max_bytes)
            except Exception as exc:
                self._logger.warning("event=stt_chunk_watch_failed detail=%s", type(exc).__name__)
                return
            if cut is None or cut <= 0 or self._stop.is_set():
                continue
            self._submit(pcm[:cut])
            self._cut += cut

    def abort(self) -> None:
        self._stop.set()
        for future in self._futures:
            future.cancel()
        self._pool.shutdown(wait=False, cancel_futures=True)

    def finish(self, final_path: Path | str | None = None, *, timeout: float = 60.0) -> str | None:
        """Stop watching, transcribe the tail, return stitched text.

        None → no chunk was cut (short clip) or something failed; caller should
        transcribe the full recording normally.
        """
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
        if final_path is not None:
            self.path = Path(final_path)
        if not self._futures:
            self._pool.shutdown(wait=False)
            return None
        tail = self._read_pcm()
        tail_future: Future | None = None
        tail_pool: ThreadPoolExecutor | None = None
        if len(tail) >= MIN_TAIL_S * BYTES_PER_S and not has_speech(tail):
            # A last second of breath/room noise is where Whisper invents
            # "Thank you for watching!" — don't transcribe it at all.
            self._logger.info("event=stt_tail_skipped reason=no_speech seconds=%.1f", len(tail) / BYTES_PER_S)
            tail = b""
        if len(tail) >= MIN_TAIL_S * BYTES_PER_S:
            # Tail runs alongside any still-pending packet, not behind it.
            tail_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vaani-stt-tail")
            chunk = write_wav(tail, self.path.parent)

            def tail_job() -> str:
                try:
                    return (self._transcribe(chunk) or "").strip()
                finally:
                    try:
                        chunk.unlink(missing_ok=True)
                    except OSError:
                        pass

            tail_future = tail_pool.submit(tail_job)
        parts: list[str] = []
        deadline = time.monotonic() + timeout
        try:
            for future in [*self._futures, *([tail_future] if tail_future else [])]:
                try:
                    text = future.result(timeout=max(0.1, deadline - time.monotonic()))
                except Exception as exc:
                    # Empty-transcript chunks (pure pause) are fine; others fail over.
                    if getattr(exc, "category", "") == "malformed":
                        continue
                    self._logger.warning(
                        "event=stt_chunk_failed detail=%s category=%s",
                        type(exc).__name__,
                        getattr(exc, "category", "-"),
                    )
                    return None
                if text:
                    parts.append(text)
        finally:
            self._pool.shutdown(wait=False)
            if tail_pool is not None:
                tail_pool.shutdown(wait=False)
        joined = " ".join(parts).strip()
        self._logger.info(
            "event=stt_chunks_joined chunks=%s tail_s=%.1f chars=%s",
            len(self._futures),
            len(tail) / BYTES_PER_S,
            len(joined),
        )
        return joined or None
