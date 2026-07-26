from pathlib import Path

from vaani.audio_upload import prepare_transcription_upload


def test_prepare_falls_back_to_wav(tmp_path: Path, monkeypatch):
    wav = tmp_path / "clip.wav"
    wav.write_bytes(b"RIFF" + b"\x00" * 64)

    # Force both compressors off.
    monkeypatch.setattr("vaani.audio_upload._try_soundfile_flac", lambda _p: None)
    monkeypatch.setattr("vaani.audio_upload._try_afconvert_m4a", lambda _p: None)

    path, name, ctype, is_temp = prepare_transcription_upload(wav)
    assert path == wav
    assert name == "clip.wav"
    assert ctype == "audio/wav"
    assert is_temp is False
