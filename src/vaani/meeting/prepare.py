"""Turn the raw meeting tracks into FLAC + transcription parts.

Streams in blocks so a 4 h meeting (~460 MB raw per track) never sits in RAM.
Mix = mic, plus the system track when on headphones (on speakers the mic
already hears remote voices). Light cleanup only: DC removal, per-track gain
to a common speech level, soft limiter. Parts are ≤ ``PART_S`` long, cut at
the quietest moment near the target, and each part after the first starts
``OVERLAP_S`` before its cut so speakers can be linked across parts.
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import soundfile as sf

RATE = 16000
BLOCK = RATE * 60
FRAME = RATE // 50  # 20 ms
PART_S = 25 * 60
OVERLAP_S = 30
SEARCH_S = 120
TARGET_RMS = 0.1  # ~ -20 dBFS on speech
MAX_GAIN = 8.0


def _blocks(path: Path):
    with open(path, "rb") as fh:
        while True:
            data = fh.read(BLOCK * 2)
            if not data:
                return
            data = data[: len(data) // 2 * 2]
            yield np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0


def _speech_gain(path: Path) -> float:
    """Gain bringing the louder (speech) frames to TARGET_RMS."""
    loud: list[float] = []
    for block in _blocks(path):
        block = block - block.mean()
        n = len(block) // FRAME
        if n == 0:
            continue
        rms = np.sqrt((block[: n * FRAME].reshape(n, FRAME) ** 2).mean(axis=1))
        loud.extend(np.sort(rms)[int(n * 0.8):].tolist())  # top 20 % of each minute
    if not loud:
        return 1.0
    level = float(np.median(loud))
    if level < 1e-4:
        return 1.0
    return min(MAX_GAIN, TARGET_RMS / level)


def prepare(directory: Path, *, mix_system: bool) -> list[dict]:
    """Write audio/{mic,system,mix}.flac and parts/part-NN.flac; return part specs.

    Raw files are deleted only after their FLAC is complete.
    """
    audio = directory / "audio"
    mic_raw, sys_raw = audio / "mic.raw", audio / "system.raw"
    gains = {"mic": _speech_gain(mic_raw)}
    if sys_raw.is_file():
        gains["system"] = _speech_gain(sys_raw)
    frame_rms: list[np.ndarray] = []
    outs = {
        "mic": sf.SoundFile(audio / "mic.flac", "w", RATE, 1, format="FLAC"),
        "mix": sf.SoundFile(audio / "mix.flac", "w", RATE, 1, format="FLAC"),
    }
    sys_iter = _blocks(sys_raw) if sys_raw.is_file() else iter(())
    if sys_raw.is_file():
        outs["system"] = sf.SoundFile(audio / "system.flac", "w", RATE, 1, format="FLAC")
    try:
        for mic in _blocks(mic_raw):
            system = next(sys_iter, None)
            outs["mic"].write(mic)
            mix = (mic - mic.mean()) * gains["mic"]
            if system is not None:
                outs["system"].write(system)
                if mix_system:
                    m = min(len(mix), len(system))
                    mix[:m] += (system[:m] - system.mean()) * gains["system"]
            mix = np.tanh(mix)  # soft limiter, never hard-clips
            outs["mix"].write(mix)
            n = len(mix) // FRAME
            if n:
                frame_rms.append(np.sqrt((mix[: n * FRAME].reshape(n, FRAME) ** 2).mean(axis=1)))
        for rest in sys_iter:  # system track can run a block longer
            outs["system"].write(rest)
    finally:
        for f in outs.values():
            f.close()
    for raw in (mic_raw, sys_raw):
        raw.unlink(missing_ok=True)
    rms = np.concatenate(frame_rms) if frame_rms else np.zeros(0)
    return write_parts(audio / "mix.flac", directory / "parts", rms)


def plan_cuts(rms: np.ndarray, *, part_s: float | None = None, search_s: float | None = None) -> list[float]:
    """Cut times (s) so each part is ≤ part_s, at the quietest 0.5 s near the limit."""
    part_s = PART_S if part_s is None else part_s
    search_s = SEARCH_S if search_s is None else search_s
    total = len(rms) * FRAME / RATE
    cuts: list[float] = []
    start = 0.0
    win = 25  # frames = 0.5 s
    while total - start > part_s:
        lo = int((start + part_s - search_s) * 50)
        hi = int((start + part_s) * 50)
        seg = rms[lo:hi]
        if len(seg) > win:
            smooth = np.convolve(seg, np.ones(win) / win, mode="valid")
            idx = int(np.argmin(smooth)) + win // 2
        else:
            idx = len(seg) // 2
        cut = (lo + idx) / 50
        cuts.append(cut)
        start = cut
    return cuts


def write_parts(mix_path: Path, parts_dir: Path, rms: np.ndarray) -> list[dict]:
    parts_dir.mkdir(exist_ok=True, mode=0o700)
    info = sf.info(str(mix_path))
    total = info.frames / RATE
    cuts = plan_cuts(rms)
    bounds = [0.0, *cuts, total]
    parts: list[dict] = []
    for i in range(len(bounds) - 1):
        cut = bounds[i]
        start = max(0.0, cut - OVERLAP_S) if i else 0.0
        end = bounds[i + 1]
        data, _ = sf.read(str(mix_path), start=int(start * RATE), stop=int(end * RATE), dtype="int16")
        path = parts_dir / f"part-{i:02d}.flac"
        sf.write(str(path), data, RATE, format="FLAC")
        os.chmod(path, 0o600)
        parts.append({"file": path.name, "start": start, "cut": cut, "end": end})
    return parts
