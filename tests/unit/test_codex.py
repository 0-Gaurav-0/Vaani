from pathlib import Path
from vaani.codex import CodexRunner

def fake(tmp_path):
    p=tmp_path/'codex'; p.write_text('#!/bin/sh\necho "$1"\necho "api_key=secret" >&2\n'); p.chmod(0o755); return str(p)
def test_runner_command_and_redaction(tmp_path):
    r=CodexRunner(fake(tmp_path), str(tmp_path)).run('hello'); assert r.stdout.strip()=='exec'; assert 'REDACTED' in r.stderr
def test_timeout(tmp_path):
    p=tmp_path/'slow'; p.write_text('#!/bin/sh\nsleep 2'); p.chmod(0o755)
    assert CodexRunner(str(p), str(tmp_path), .01).run('x').timed_out

def test_default_workspace_uses_configured_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("VAANI_ASSISTANT_CWD", str(tmp_path))
    assert CodexRunner().cwd == str(tmp_path)
