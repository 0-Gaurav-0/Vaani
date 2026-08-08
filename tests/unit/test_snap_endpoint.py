import math
import struct
import time
import wave
from pathlib import Path

from vaani.snap_endpoint import SnapSessionWatcher


def _write_wav(path: Path, samples: list[int], rate: int = 16000) -> None:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(struct.pack("<%dh" % len(samples), *samples))


def _speech(n: int = 2400) -> list[int]:
    return [
        int(9000 * math.sin(i * 0.37) + 2500 * math.sin(i * 1.1)) for i in range(n)
    ]


def _click_frame() -> list[int]:
    # One 10ms frame with a sharp transient (high crest after HP).
    return [0] * 20 + [24000, -18000, 9000] + [0] * 137


def test_silence_endpoint_stops(tmp_path, monkeypatch):
    monkeypatch.setenv("VAANI_SNAP_STOP_GRACE", "0")
    monkeypatch.setenv("VAANI_SNAP_SILENCE_S", "0.2")
    monkeypatch.setenv("VAANI_SNAP_SPEECH_FLOOR", "0.02")
    monkeypatch.setenv("VAANI_SNAP_SILENCE_END", "1")
    path = tmp_path / "rec.wav"
    # ~150ms speech + 300ms silence
    samples = _speech(2400) + [0] * 4800
    _write_wav(path, samples)
    stopped: list[bool] = []

    watcher = SnapSessionWatcher(path, on_stop=lambda: stopped.append(True))
    watcher.start()
    watcher._started = time.monotonic() - 1.0  # grace already elapsed
    deadline = time.time() + 2.0
    while not stopped and time.time() < deadline:
        time.sleep(0.05)
    watcher.stop()
    assert stopped, "expected silence endpoint to fire"


def test_tap_burst_without_speech_cancels(tmp_path, monkeypatch):
    monkeypatch.setenv("VAANI_SNAP_STOP_GRACE", "0.9")
    monkeypatch.setenv("VAANI_SNAP_SILENCE_END", "0")
    monkeypatch.setenv("VAANI_SNAP_THRESHOLD", "0.05")
    monkeypatch.setenv("VAANI_SNAP_RATIO", "6.0")
    path = tmp_path / "taps.wav"
    quiet = [0] * 800  # 50ms
    # Several isolated clicks with quiet pads (typing / lid taps).
    samples: list[int] = []
    for _ in range(4):
        samples.extend(quiet)
        samples.extend(_click_frame())
        samples.extend(quiet)
        samples.extend(quiet)
    _write_wav(path, samples)
    stopped: list[str] = []
    cancelled: list[str] = []

    watcher = SnapSessionWatcher(
        path,
        on_stop=lambda: stopped.append("stop"),
        on_cancel=lambda: cancelled.append("cancel"),
    )
    watcher.start()
    watcher._started = time.monotonic() - 0.4
    deadline = time.time() + 2.5
    while not cancelled and not stopped and time.time() < deadline:
        time.sleep(0.05)
    watcher.stop()
    assert cancelled, "expected tap-burst to Esc-cancel"
    assert not stopped


def test_no_speech_timeout_cancels(tmp_path, monkeypatch):
    monkeypatch.setenv("VAANI_SNAP_STOP_GRACE", "0.2")
    monkeypatch.setenv("VAANI_SNAP_SILENCE_END", "1")
    monkeypatch.setenv("VAANI_SNAP_SPEECH_FLOOR", "0.05")
    path = tmp_path / "quiet.wav"
    _write_wav(path, [0] * 32000)  # 2s silence
    cancelled: list[str] = []
    stopped: list[str] = []
    watcher = SnapSessionWatcher(
        path,
        on_stop=lambda: stopped.append("stop"),
        on_cancel=lambda: cancelled.append("cancel"),
    )
    watcher.start()
    watcher._started = time.monotonic() - 1.2
    deadline = time.time() + 2.0
    while not cancelled and not stopped and time.time() < deadline:
        time.sleep(0.05)
    watcher.stop()
    assert cancelled, "expected empty false-start to cancel"
    assert not stopped
