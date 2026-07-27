import os, signal, stat, time
from vaani.audio import AudioRecorder, AudioError
from pathlib import Path

def test_escalation_reaps_and_stop_is_idempotent(tmp_path):
    script = tmp_path/'parec'; script.write_text('#!/bin/sh\ntrap "" INT TERM\nwhile :; do :; done\n'); script.chmod(stat.S_IRWXU)
    rec = AudioRecorder(tmp_path/'audio', parec=str(script)); rec.start()
    with __import__('pytest').raises(AudioError): rec.stop()
    assert rec._proc is None

def test_normal_sigint_stop_reaps_and_cleanup_deletes(tmp_path):
    fixture = Path(__file__).parents[1] / 'fixtures' / 'valid.wav'
    script = tmp_path/'parec'; script.write_text(f'#!/bin/sh\ncat "{fixture}" & wait\n'); script.chmod(stat.S_IRWXU)
    rec = AudioRecorder(tmp_path/'audio', parec=str(script)); rec.start()
    result = rec.stop()
    assert result.path.exists() and rec._proc is None
    again = rec.stop(); assert again.path == result.path
    rec.cleanup(); assert not result.path.exists()
