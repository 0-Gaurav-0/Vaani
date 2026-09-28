import math
import struct
import time
import wave
from pathlib import Path

from vaani.stt_chunks import BYTES_PER_S, ChunkedTranscriber, find_cut


def _tone(seconds: float, amp: float = 0.3) -> bytes:
    n = int(16000 * seconds)
    return struct.pack("<%dh" % n, *(int(amp * 32767 * math.sin(i * 0.3)) for i in range(n)))


def _silence(seconds: float) -> bytes:
    return b"\x00\x00" * int(16000 * seconds)


def _wav(path: Path, pcm: bytes) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm)
    return path


def test_find_cut_waits_for_min_then_cuts_in_pause():
    pcm = _tone(1.2) + _silence(0.6) + _tone(1.0)
    assert find_cut(pcm, min_bytes=int(3 * BYTES_PER_S), max_bytes=10**9) is None
    cut = find_cut(pcm, min_bytes=int(1.0 * BYTES_PER_S), max_bytes=10**9)
    assert cut is not None
    assert 1.2 * BYTES_PER_S <= cut <= 1.8 * BYTES_PER_S


def test_find_cut_ignores_pause_before_min():
    pcm = _tone(0.5) + _silence(0.6) + _tone(2.0)
    assert find_cut(pcm, min_bytes=int(1.5 * BYTES_PER_S), max_bytes=10**9) is None


def test_find_cut_forces_cut_past_max_without_pause():
    pcm = _tone(3.0)
    cut = find_cut(pcm, min_bytes=int(1.0 * BYTES_PER_S), max_bytes=int(2.5 * BYTES_PER_S))
    assert cut is not None and cut >= 1.0 * BYTES_PER_S


def _durations(calls):
    out = []
    for p in calls:
        with wave.open(str(p), "rb") as w:
            out.append(round(w.getnframes() / 16000, 1))
    return out


def test_chunker_transcribes_packets_and_tail(tmp_path):
    pcm = _tone(1.2) + _silence(0.6) + _tone(1.2) + _silence(0.6) + _tone(0.8)
    path = _wav(tmp_path / "rec.wav", pcm)
    seen, texts = [], iter(["one", "two", "three", "four"])

    def transcribe(chunk: Path) -> str:
        seen.append(chunk)
        _ = _durations([chunk])  # file must exist + be a valid WAV while transcribing
        return next(texts)

    c = ChunkedTranscriber(path, transcribe, min_chunk_s=1.0, max_chunk_s=30, poll_s=0.01).start()
    deadline = time.monotonic() + 3
    while c.chunks < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert c.chunks == 2
    assert c.finish(path) == "one two three"
    assert not any(p.exists() for p in seen)  # temp chunks cleaned up


def test_short_clip_returns_none_for_normal_path(tmp_path):
    path = _wav(tmp_path / "rec.wav", _tone(2.0))
    c = ChunkedTranscriber(path, lambda p: "x", min_chunk_s=20, poll_s=0.01).start()
    time.sleep(0.05)
    assert c.finish(path) is None


def test_chunk_failure_falls_back(tmp_path):
    pcm = _tone(1.2) + _silence(0.6) + _tone(1.0)
    path = _wav(tmp_path / "rec.wav", pcm)

    def boom(_p):
        raise RuntimeError("http")

    c = ChunkedTranscriber(path, boom, min_chunk_s=1.0, poll_s=0.01).start()
    deadline = time.monotonic() + 3
    while c.chunks < 1 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert c.finish(path) is None
