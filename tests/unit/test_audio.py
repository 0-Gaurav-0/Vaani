import os, shutil, stat, time, wave
from pathlib import Path
import pytest
from vaani.audio import AudioError, AudioRecorder, preflight, validate_wav
from vaani.config import sweep_audio_directory

def wav(path, seconds=.3):
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b'\0\0' * int(16000*seconds))

def exe(path, text):
    path.write_text('#!/bin/sh\n' + text); path.chmod(stat.S_IRWXU); return str(path)

def test_validate_wav_and_secure_start(tmp_path):
    p = tmp_path/'ok.wav'; wav(p)
    assert validate_wav(p).duration_seconds >= .25
    rec = AudioRecorder(tmp_path/'audio', parec=exe(tmp_path/'parec',f'cat {p}'))
    result = rec.start(); assert result.path.stat().st_mode & 0o777 == 0o600
    rec.cleanup()

def test_preflight_is_read_only(tmp_path):
    log = tmp_path/'calls'; log.write_text('')
    p = exe(tmp_path/'parec', f'echo "$@" >> {log}\n[ "$1" = --help ] && exit 0\necho wav\nexit 0')
    pactl = exe(tmp_path/'pactl', 'echo source')
    preflight(parec=p, pactl=pactl)
    assert log.read_text().splitlines() == ['--help', '--list-file-formats']

def test_exact_argv_and_env_allowlist(tmp_path):
    args = tmp_path/'args'; env = tmp_path/'env'
    p = exe(tmp_path/'parec', f'printf "%s\\n" "$@" > {args}; env > {env}; sleep 1')
    rec = AudioRecorder(tmp_path/'audio', parec=p, env={'PATH':'/bin','GROQ_API_KEY':'secret','HOME':'/tmp'})
    rec.start()
    # Spawn+shell can be slow under load; wait for the argv capture file.
    for _ in range(100):
        if args.exists():
            break
        time.sleep(0.05)
    lines = args.read_text().splitlines(); rec.cleanup()
    assert lines == ['--device=@DEFAULT_SOURCE@','--rate=16000','--channels=1','--format=s16le','--file-format=wav']
    assert 'GROQ_API_KEY=' not in env.read_text()

def test_invalid_directory_and_open_failure(tmp_path):
    bad = tmp_path/'link'; bad.symlink_to(tmp_path/'missing', target_is_directory=True)
    with pytest.raises(AudioError): AudioRecorder(bad)
    # Prefer a real failing binary so spawn latency does not race the crash check.
    failing = shutil.which("false") or exe(tmp_path/'fail', 'exit 1')
    with pytest.raises(AudioError, match='source open failed'): AudioRecorder(tmp_path/'a', parec=failing).start()

@pytest.mark.parametrize('seconds', [0.1, 600.1])
def test_duration_boundaries(tmp_path, seconds):
    p=tmp_path/'x.wav'; wav(p, seconds)
    with pytest.raises(AudioError): validate_wav(p)

def test_startup_sweep_owned_files_only(tmp_path):
    d=tmp_path/'audio'; d.mkdir(mode=0o755)
    own=d/'old.wav'; own.write_bytes(b'x'); own.chmod(0o644)
    link=d/'link'; link.symlink_to(own)
    sweep_audio_directory(d, uid=os.getuid())
    assert not own.exists() and link.is_symlink()
