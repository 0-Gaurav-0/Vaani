import os
from vaani.config import *

def test_timeout_constants():
    assert (CONNECT_TIMEOUT, UPLOAD_TIMEOUT, TRANSCRIPTION_READ_TIMEOUT, TRANSCRIPTION_DEADLINE) == (5.0, 30.0, 120.0, 150.0)

def test_umask_restored(tmp_path, monkeypatch):
    seen = []
    real = os.umask
    monkeypatch.setattr(os, "umask", lambda value: (seen.append(value) or real(value)))
    Settings.from_home(tmp_path).prepare()
    assert seen[0] == 0o077 and seen[-1] != 0o077
def test_secure_paths_and_sweep(tmp_path):
    s = Settings.from_home(tmp_path); s.prepare()
    assert s.audio_dir.stat().st_mode & 0o777 == 0o700
    p = s.audio_dir / "old.wav"; p.write_bytes(b"x"); p.chmod(0o644)
    link = s.audio_dir / "link"; link.symlink_to(p)
    assert p in sweep_audio_directory(s.audio_dir); assert link.is_symlink()

def test_history_mode_and_sweep_chmod_before_unlink(tmp_path, monkeypatch):
    s = Settings.from_home(tmp_path); s.prepare(); s.history_db.write_text("x"); s.history_db.chmod(0o644); s.prepare()
    assert s.history_db.stat().st_mode & 0o777 == 0o600
    p = s.audio_dir / "x.wav"; p.write_bytes(b"x"); events = []
    old_chmod, old_unlink = Path.chmod, Path.unlink
    monkeypatch.setattr(Path, "chmod", lambda self, mode: (events.append("chmod"), old_chmod(self, mode))[1])
    monkeypatch.setattr(Path, "unlink", lambda self: (events.append("unlink"), old_unlink(self))[1])
    sweep_audio_directory(s.audio_dir)
    assert events[:2] == ["chmod", "unlink"]
def test_child_environment_excludes_key():
    assert "GROQ_API_KEY" not in child_environment({"PATH":"x", "GROQ_API_KEY":"CANARY_KEY", "AWS_SECRET_ACCESS_KEY":"x"})
    assert child_environment({}) == {}
    assert "GROQ_API_KEY" not in child_environment(None)
