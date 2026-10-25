import os
import time
from pathlib import Path

import soundfile as sf

from vaani.session_archive import SessionArchive, recover_recordings, sweep_sessions
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
    assert (directory / "transcript.txt").stat().st_mode & 0o777 == 0o600
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


def test_audio_is_on_disk_before_upload_starts(tmp_path):
    seen = []

    def transcribe(_p):
        # Upload may die here; the chunk must already be archived.
        seen.append(sorted(p.name for p in (tmp_path / "sessions").rglob("chunk-*.flac")))
        return "x"

    _run(tmp_path, transcribe)
    assert seen[0] == ["chunk-000.flac"]


def test_crashed_recording_is_recovered_as_flac_not_swept(tmp_path):
    from vaani.config import sweep_audio_directory

    audio_dir = tmp_path / "audio"
    audio_dir.mkdir()
    _wav(audio_dir / "recording-abc.wav", _tone(2.0))
    (audio_dir / "recording-tiny.wav").write_bytes(b"RIFF")
    moved = recover_recordings(audio_dir, tmp_path / "sessions", background=False)
    sweep_audio_directory(audio_dir)
    assert len(moved) == 1 and moved[0].parent.name.endswith("-recovered")
    flac = moved[0].with_suffix(".flac")
    assert flac.is_file() and not moved[0].exists()
    assert abs(sf.info(str(flac)).duration - 2.0) < 0.05
