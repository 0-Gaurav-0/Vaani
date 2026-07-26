"""macOS audio recorder wrapping the shared sounddevice backend."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..audio_common import SoundDeviceRecorder


class MacAudioRecorder(SoundDeviceRecorder):
    """Record 16 kHz mono WAV via PortAudio / sounddevice."""

    def __init__(self, audio_dir: Path, *, device: Any = None):
        super().__init__(audio_dir, device=device)


AudioRecorderImpl = MacAudioRecorder
