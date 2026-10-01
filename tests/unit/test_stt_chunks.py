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


def test_trim_edge_silence_keeps_speech_with_padding():
    import numpy as np
    from vaani.audio_upload import trim_edge_silence

    rng = np.random.default_rng(0)
    sil = (rng.standard_normal(32000) * 50).astype("int16")
    speech = (np.sin(np.arange(16000) * 0.3) * 8000).astype("int16")
    out = trim_edge_silence(np.concatenate([sil, speech, sil]), 16000)
    assert 1.5 <= len(out) / 16000 <= 1.7  # 1s speech + 0.3s pad each side
    assert len(trim_edge_silence(speech, 16000)) == len(speech)  # nothing to trim
    quiet = (rng.standard_normal(48000) * 50).astype("int16")
    assert len(trim_edge_silence(quiet, 16000)) == len(quiet)  # no speech → untouched


def test_parallel_hinglish_starts_hi_without_waiting(tmp_path):
    import threading
    from types import SimpleNamespace

    from vaani.groq import GroqClient, TranscriptResult

    path = _wav(tmp_path / "a.wav", _tone(1.0))
    started, gate = [], threading.Event()
    client = GroqClient()

    def fake(p, key, *, language=None, **_k):
        started.append(language)
        if language == "en":
            gate.wait(2)  # en blocks until hi has started
            return TranscriptResult("mera gaana chalao yaar", "en")
        gate.set()
        return TranscriptResult("मेरा गाना चलाओ यार", "hi")

    client.transcribe = fake
    out = client.transcribe_hinglish(path, "k", parallel=True)
    assert set(started) == {"en", "hi"}
    assert out.text
    client.close()


def test_silent_tail_is_not_transcribed(tmp_path):
    from vaani.stt_chunks import has_speech

    assert has_speech(_tone(1.0)) and not has_speech(_silence(1.5))
    pcm = _tone(1.2) + _silence(0.6) + _tone(1.0) + _silence(1.5)  # trailing breath/noise only
    path = _wav(tmp_path / "rec.wav", pcm)
    seen = []

    def transcribe(chunk):
        # Whisper hallucinates the outro only on silent audio.
        with wave.open(str(chunk), "rb") as w:
            pcm_chunk = w.readframes(w.getnframes())
        seen.append(has_speech(pcm_chunk))
        return "real words" if has_speech(pcm_chunk) else "Thank you for watching!"

    c = ChunkedTranscriber(path, transcribe, min_chunk_s=1.0, poll_s=0.01).start()
    deadline = time.monotonic() + 3
    while c.chunks < 1 and time.monotonic() < deadline:
        time.sleep(0.01)
    out = c.finish(path)
    assert out and "watching" not in out
    assert all(seen)  # the silent tail was never sent
