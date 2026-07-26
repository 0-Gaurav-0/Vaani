"""Windows microphone capture via the shared sounddevice recorder."""
from __future__ import annotations

from ..audio_common import SoundDeviceRecorder


class WindowsAudioRecorder(SoundDeviceRecorder):
    """16 kHz mono WAV recorder for the Windows PlatformBundle."""
