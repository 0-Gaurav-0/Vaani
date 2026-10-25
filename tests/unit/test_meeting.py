import json
import time
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from vaani.meeting import assemble as asm
from vaani.meeting import prepare as prep
from vaani.meeting import worker
from vaani.meeting.control import MeetingControl
from vaani.meeting.gemini import QuotaError, parse_words
from vaani.meeting.job import PREPARED, RECORDED, RECORDING, WRITTEN, MeetingJob
from vaani.meeting.recorder import CLIENT_MARKER, parec_argv


# ---- job -----------------------------------------------------------------

def test_job_roundtrip_and_due(tmp_path):
    job = MeetingJob(tmp_path, started=1.0)
    job.save()
    job.advance(RECORDED, stopped=5.0)
    again = MeetingJob.load(tmp_path)
    assert again.state == RECORDED and again.stopped == 5.0
    again.fail("quota", retry_after=time.time() + 100)
    assert not MeetingJob.load(tmp_path).due()
    assert MeetingJob.load(tmp_path).due(now=time.time() + 200)
    assert (tmp_path / "job.json").stat().st_mode & 0o777 == 0o600


# ---- recorder ------------------------------------------------------------

def test_meeting_parec_is_never_matched_by_dictation_reaper(monkeypatch):
    from vaani import audio as audio_mod

    line = " ".join(parec_argv("@DEFAULT_SOURCE@"))
    assert CLIENT_MARKER in line
    seen = {"n": 0}

    def check_output(argv, **kwargs):
        seen["n"] += 1
        return f" 3333 {line}\n" if seen["n"] == 1 else ""

    kills = []
    monkeypatch.setattr(audio_mod.subprocess, "check_output", check_output)
    monkeypatch.setattr(audio_mod.os, "kill", lambda pid, sig: kills.append(pid))
    monkeypatch.setattr(audio_mod.time, "sleep", lambda _s: None)
    assert audio_mod.reap_orphan_parec() == 0 and kills == []


# ---- prepare -------------------------------------------------------------

def _raw(path: Path, signal: np.ndarray) -> None:
    path.write_bytes((np.clip(signal, -1, 1) * 32767).astype("<i2").tobytes())


def _speechy(seconds: float, gap_at: float | None = None) -> np.ndarray:
    t = np.arange(int(16000 * seconds)) / 16000
    x = 0.05 * np.sin(2 * np.pi * 200 * t)
    if gap_at is not None:
        x[int(gap_at * 16000): int((gap_at + 1.0) * 16000)] = 0
    return x


def test_plan_cuts_picks_the_pause_near_the_limit():
    rms = np.full(50 * 100, 0.1)  # 100 s of "speech" at 50 frames/s
    rms[50 * 80: 50 * 81] = 0.0  # pause at 80-81 s
    cuts = prep.plan_cuts(rms, part_s=90, search_s=30)
    assert len(cuts) == 1 and 80 <= cuts[0] <= 81


def test_prepare_writes_flac_and_overlapping_parts(tmp_path, monkeypatch):
    monkeypatch.setattr(prep, "PART_S", 40)
    monkeypatch.setattr(prep, "SEARCH_S", 15)
    monkeypatch.setattr(prep, "OVERLAP_S", 5)
    audio = tmp_path / "audio"
    audio.mkdir()
    _raw(audio / "mic.raw", _speechy(70, gap_at=33))
    _raw(audio / "system.raw", _speechy(70) * 0.5)
    parts = prep.prepare(tmp_path, mix_system=True)
    for name in ("mic", "system", "mix"):
        assert abs(sf.info(str(audio / f"{name}.flac")).duration - 70) < 0.1
    assert not (audio / "mic.raw").exists() and not (audio / "system.raw").exists()
    assert len(parts) == 2
    assert 33 <= parts[1]["cut"] <= 34 and parts[1]["start"] == pytest.approx(parts[1]["cut"] - 5)
    assert parts[0]["end"] == parts[1]["cut"]
    for p in parts:
        assert (tmp_path / "parts" / p["file"]).is_file()


# ---- gemini parsing + assembly -------------------------------------------

def _body(words):
    """words: [(text, start, speaker)] → interactions-style body (one block)."""
    text, anns, pos = "", [], 0
    for w, start, spk in words:
        si = len(text)
        text += w + " "
        anns.append({"type": "word_info", "text": w, "speaker": spk, "start_index": si,
                     "end_index": si + len(w), "start_offset": f"{start:.3f}s", "end_offset": f"{start + 0.3:.3f}s"})
    return {"status": "completed", "steps": [{"type": "model_output", "content": [
        {"type": "text", "text": text.strip(), "annotations": anns}]}]}


def test_parse_words_reads_offsets_and_speakers():
    blocks, words = parse_words(_body([("Hello", 0.1, "spk:0"), ("world", 0.5, "spk:1")]))
    assert blocks == ["Hello world"]
    assert words[1].start == pytest.approx(0.5) and words[1].speaker == "spk:1"


def test_assemble_links_speakers_across_parts_and_drops_overlap_duplicates():
    parts = [{"start": 0.0, "cut": 0.0, "end": 20.0}, {"start": 10.0, "cut": 20.0, "end": 40.0}]
    # Part 0: A speaks 0-9, B speaks 12-19 (overlap region 10-20 is B).
    p0 = _body([("alpha", 1, "spk:0"), ("one", 2, "spk:0"), ("bravo", 12, "spk:1"),
                ("two", 14, "spk:1"), ("three", 16, "spk:1")])
    # Part 1 (local time): its labels are swapped; B at 2-6 (abs 12-16), A after cut.
    p1 = _body([("bravo", 2, "spk:0"), ("two", 4, "spk:0"), ("three", 6, "spk:0"),
                ("charlie", 12, "spk:1"), ("four", 14, "spk:1")])
    turns = asm.assemble(parts, [p0, p1])
    assert [(t.speaker, t.text) for t in turns] == [
        (1, "alpha one"), (2, "bravo two three"), (3, "charlie four")]
    md = asm.to_markdown("M", turns, {2: "Rahul"})
    assert "**Rahul** [00:00:12]" in md and "**Speaker 3** [00:00:22]" in md


def test_assemble_maps_returning_speaker_to_same_id():
    parts = [{"start": 0.0, "cut": 0.0, "end": 20.0}, {"start": 10.0, "cut": 20.0, "end": 40.0}]
    p0 = _body([("hi", 1, "spk:0"), ("same", 12, "spk:0"), ("words", 14, "spk:0")])
    p1 = _body([("same", 2, "spk:3"), ("words", 4, "spk:3"), ("later", 15, "spk:3")])
    turns = asm.assemble(parts, [p0, p1])
    assert [(t.speaker, t.text) for t in turns] == [(1, "hi same words later")]


# ---- worker --------------------------------------------------------------

class FakeTranscriber:
    def __init__(self, fail_first=False):
        self.calls, self.fail_first = 0, fail_first

    def transcribe(self, path):
        self.calls += 1
        if self.fail_first and self.calls == 1:
            raise QuotaError(120)
        return _body([("namaste", 0.5, "spk:0"), ("hello", 1.5, "spk:1")])


def _recorded_job(tmp_path):
    d = tmp_path / "2026-10-23 1530"
    (d / "audio").mkdir(parents=True)
    _raw(d / "audio" / "mic.raw", _speechy(3))
    _raw(d / "audio" / "system.raw", _speechy(3))
    MeetingJob(d, state=RECORDED, started=1.0, stopped=4.0).save()
    return d


def test_worker_pauses_on_quota_then_resumes_without_respending(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "notify", lambda *a, **k: None)
    d = _recorded_job(tmp_path)
    fake = FakeTranscriber(fail_first=True)
    assert worker.process_all(tmp_path, fake) == 1
    job = MeetingJob.load(d)
    assert job.state == PREPARED and job.retry_after > time.time() and "quota" in job.error
    job.retry_after = 0
    job.save()
    assert worker.process_all(tmp_path, fake) == 0
    assert MeetingJob.load(d).state == WRITTEN and fake.calls == 2
    md = (d / "transcript.md").read_text()
    assert "**Speaker 1** [00:00:00]" in md and "namaste" in md and "**Speaker 2**" in md
    assert worker.process_all(tmp_path, fake) == 0 and fake.calls == 2  # nothing re-sent


def test_worker_without_key_keeps_audio_and_retries_later(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, "notify", lambda *a, **k: None)
    d = _recorded_job(tmp_path)
    assert worker.process_all(tmp_path, None) == 1
    job = MeetingJob.load(d)
    assert job.state == PREPARED and "GEMINI_API_KEY" in job.error
    assert (d / "audio" / "mix.flac").is_file()


# ---- control ----------------------------------------------------------

class FakeRecorder:
    def __init__(self):
        self.active, self.elapsed, self.starts, self.stops = False, 0.0, 0, 0
        self.job = None
        self.paused_flag = False

    def start(self):
        self.active = True
        self.starts += 1
        self.job = MeetingJob(Path("/tmp/x"), started=time.time())
        return self.job

    def stop(self):
        self.active = False
        self.stops += 1
        return MeetingJob(Path("/tmp/x"), state=RECORDED)

    def pause(self):
        if self.paused_flag:
            return False
        self.paused_flag = True
        self.job.paused_since = time.time()
        return True

    def resume(self):
        if not self.paused_flag:
            return False
        self.paused_flag = False
        self.job.paused_since = 0.0
        return True

    def level(self):
        return 0.3


class FakePill:
    def __init__(self):
        self.shown = self.hidden = 0
        self.levels, self.commands, self.updates = [], [], []

    def show(self, **kw):
        self.shown += 1

    def update(self, **kw):
        self.updates.append(kw)

    def level(self, v):
        self.levels.append(v)

    def poll_command(self):
        return self.commands.pop(0) if self.commands else None

    def hide(self):
        self.hidden += 1


def _control(monkeypatch, **kw):
    import vaani.meeting.control as ctl
    monkeypatch.setattr(ctl, "notify", lambda *a, **k: None)
    spawned, cues = [], []
    rec, pill = FakeRecorder(), FakePill()
    c = MeetingControl(Path("/tmp/meet"), recorder=rec, pill=pill, sound=cues.append,
                       spawn=lambda: spawned.append(1), **kw)
    return c, rec, pill, spawned, cues


def _wait(cond, timeout=2.0):
    deadline = time.monotonic() + timeout
    while not cond() and time.monotonic() < deadline:
        time.sleep(0.01)
    return cond()


def test_control_r_starts_single_r_ignored_rr_stops_and_pill_hides(monkeypatch):
    c, rec, pill, spawned, cues = _control(monkeypatch)
    assert c.shortcut(1) and rec.active and pill.shown == 1
    assert _wait(lambda: pill.levels)  # waveform fed while recording
    assert not c.shortcut(1) and rec.active  # stray tap does not stop
    assert c.shortcut(2) and not rec.active and spawned == [1]
    assert pill.hidden == 1  # gone immediately, no processing pill
    assert cues == ["start", "busy", "stop"]
    assert c.shortcut(1) and rec.starts == 2  # next meeting right away


def test_control_pill_pause_resume_stop(monkeypatch):
    c, rec, pill, spawned, cues = _control(monkeypatch)
    c.shortcut(1)
    pill.commands += ["pause"]
    assert _wait(lambda: rec.paused_flag)
    assert pill.updates[-1]["paused_since"] > 0
    pill.commands += ["resume"]
    assert _wait(lambda: not rec.paused_flag)
    pill.commands += ["stop"]
    assert _wait(lambda: not rec.active) and spawned == [1] and pill.hidden == 1


def test_control_cap_counts_recorded_time_and_processes(monkeypatch):
    import vaani.meeting.control as ctl
    monkeypatch.setattr(ctl, "WARN_BEFORE_MAX_S", 1)
    c, rec, pill, spawned, cues = _control(monkeypatch, max_s=10)
    c.shortcut(1)
    rec.elapsed = 9.5
    assert _wait(lambda: "warn" in cues)
    assert rec.active
    rec.elapsed = 10.0
    assert _wait(lambda: not rec.active) and spawned == [1]


# ---- hotkey --------------------------------------------------------------

def test_hotkey_r_taps_abort_dictation_and_count(monkeypatch):
    from vaani.hotkeys import MiddleButtonHotkeyManager

    now = [0.0]
    meetings, aborts = [], []
    m = MiddleButtonHotkeyManager(lambda mode: True, clock=lambda: now[0],
                                  on_meeting=meetings.append, on_abort_quiet=lambda: aborts.append(1))
    monkeypatch.setattr(m, "_trackpoint_middle_state", lambda: True)
    m._session_active = True  # 150 ms hold already started dictation
    m.on_r_key(True); m.on_r_key(False)
    now[0] = 0.2
    m.on_r_key(True); m.on_r_key(True)  # autorepeat ignored
    m.on_r_key(False)
    m.meeting_tick()
    assert meetings == []  # window still open
    now[0] = 0.8
    m.meeting_tick()
    assert meetings == [2] and aborts == [1]
    assert not m._session_active and m._meeting_hold and m._ignore_until_up


def test_hotkey_meeting_hold_release_resets_gesture(monkeypatch):
    from vaani import hotkeys as hk

    now = [0.0]
    m = hk.MiddleButtonHotkeyManager(lambda mode: True, clock=lambda: now[0], on_meeting=lambda n: None)
    monkeypatch.setattr(m, "_trackpoint_middle_state_for_press", lambda: True)
    middle = [True]
    monkeypatch.setattr(m, "_trackpoint_middle_state", lambda: middle[0])
    monkeypatch.setattr(m, "_media_topology_is_verified", lambda: False)
    m._handle_button_event(4)  # press
    m.on_r_key(True); m.on_r_key(False)
    middle[0] = False
    m._handle_button_event(5)  # release
    assert m._button_source is None and not m._gesture.down and m._gesture.armed_until < 0
    assert not m._ignore_until_up and not m._meeting_hold


class FakeRoot:
    def __init__(self):
        self.grabbed = 0
        self.log = []

    def grab_key(self, *a):
        self.grabbed += 1
        self.log.append("grab")

    def ungrab_key(self, *a):
        self.grabbed -= 1
        self.log.append("ungrab")


def _manager_with_grab(monkeypatch, middle):
    from vaani import hotkeys as hk

    meetings = []
    m = hk.MiddleButtonHotkeyManager(lambda mode: True, on_meeting=meetings.append, on_cancel=lambda: None)
    m._r_keycode, m._root = 27, FakeRoot()
    m._dpy = type("D", (), {"flush": lambda self: None})()
    monkeypatch.setattr(m, "_trackpoint_middle_state_for_press", lambda: True)
    monkeypatch.setattr(m, "_trackpoint_middle_state", lambda: middle[0])
    monkeypatch.setattr(m, "_media_topology_is_verified", lambda: False)
    return m, meetings


def test_r_grab_released_on_poll_release_and_esc(monkeypatch):
    middle = [True]
    m, _ = _manager_with_grab(monkeypatch, middle)
    m._handle_button_event(4)
    assert m._root.grabbed == 1
    middle[0] = False
    m._sync_trackpoint_release()  # release event missed; poll catches it
    assert m._root.grabbed == 0
    middle[0] = True
    m._handle_button_event(4)
    assert m._root.grabbed == 1
    m._cancel_now()  # Esc
    assert m._root.grabbed == 0


def test_r_with_middle_up_never_starts_meeting(monkeypatch):
    middle = [True]
    m, meetings = _manager_with_grab(monkeypatch, middle)
    m._handle_button_event(4)
    middle[0] = False  # release lost, grab stale
    m.on_r_key(True)
    m._r_last = -10
    m.meeting_tick()
    assert meetings == [] and m._root.grabbed == 0


def test_recorder_pause_resume_appends_and_excludes_paused_time(tmp_path, monkeypatch):
    import stat
    from vaani.meeting import recorder as recmod

    fake = tmp_path / "parec"
    fake.write_text("#!/bin/sh\ntrap 'exit 0' INT TERM\nwhile :; do printf 'abcd'; sleep 0.01; done\n")
    fake.chmod(stat.S_IRWXU)
    monkeypatch.setattr(recmod, "scoped", lambda argv: argv)
    monkeypatch.setattr(recmod, "output_kind", lambda: "speaker")
    r = recmod.MeetingRecorder(tmp_path / "meet", parec=str(fake))
    job = r.start()
    mic = job.dir / "audio" / "mic.raw"
    time.sleep(0.3)
    assert r.pause() and not r.pause()
    size_paused = mic.stat().st_size
    assert size_paused > 0
    time.sleep(0.4)
    assert mic.stat().st_size == size_paused  # nothing captured while paused
    assert r.level() == 0.0
    paused_elapsed = r.elapsed
    time.sleep(0.2)
    assert r.elapsed == pytest.approx(paused_elapsed, abs=0.05)  # clock frozen
    assert r.resume()
    time.sleep(0.3)
    assert mic.stat().st_size > size_paused  # appended, not truncated
    done = r.stop()
    assert done.state == RECORDED and done.paused_total >= 0.4 and done.paused_since == 0.0
    assert not [p for p in job.pids if recmod._alive_meeting_pid(p)]


def test_adopt_keeps_a_meeting_paused_at_crash(tmp_path):
    from vaani.meeting import recorder as recmod

    d = tmp_path / "2026-10-23 0900"
    (d / "audio").mkdir(parents=True)
    MeetingJob(d, state=RECORDING, started=time.time() - 60, paused_since=time.time() - 10).save()
    r = recmod.MeetingRecorder(tmp_path)
    assert r.adopt() == [] and r.active and r.paused
