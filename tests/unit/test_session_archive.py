import os
import time
from pathlib import Path

import soundfile as sf

from vaani.session_archive import SessionArchive, sweep_sessions
from vaani.stt_chunks import ChunkedTranscriber

from test_stt_chunks import _silence, _tone, _wav


def _run(tmp_path, transcribe):
    pcm = _tone(1.2) + _silence(0.6) + _tone(1.2) + _silence(0.6) + _tone(0.8)
    path = _wav(tmp_path / "rec.wav", pcm)
    archive = SessionArchive(tmp_path / "sessions")
    c = ChunkedTranscriber(path, transcribe, min_chunk_s=1.0, max_chunk_s=30, poll_s=0.01, archive=archive).start()
    deadline = time.monotonic() + 3
    while c.chunks < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    return c.finish(path), archive.directory


def test_chunks_and_tail_are_archived_as_flac_with_transcript(tmp_path):
    texts = iter(["one", "two", "three"])
    result, directory = _run(tmp_path, lambda _p: next(texts))
    assert result == "one two three"
    flacs = sorted(directory.glob("chunk-*.flac"))
    assert [p.name for p in flacs] == ["chunk-000.flac", "chunk-001.flac", "chunk-002.flac"]
    assert all(sf.info(str(p)).samplerate == 16000 for p in flacs)
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in flacs)
    lines = sorted((directory / "transcript.txt").read_text().splitlines())
    assert lines == ["[000] one", "[001] two", "[002] three"]


def test_audio_is_kept_when_transcription_fails(tmp_path):
    def boom(_p):
        raise RuntimeError("http")

    result, directory = _run(tmp_path, boom)
    assert result is None
    assert (directory / "chunk-000.flac").is_file()
    assert "[000] <no transcript>" in (directory / "transcript.txt").read_text()


def test_sweep_removes_only_expired_sessions(tmp_path):
    root = tmp_path / "sessions"
    old, new = root / "old", root / "new"
    old.mkdir(parents=True)
    new.mkdir()
    (old / "transcript.txt").write_text("x")
    past = time.time() - 3 * 24 * 3600
    os.utime(old, (past, past))
    assert sweep_sessions(root, max_age_s=48 * 3600) == 1
    assert not old.exists() and new.exists()
