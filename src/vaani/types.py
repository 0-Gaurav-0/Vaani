from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Mapping, Protocol

class AppState(str, Enum):
    IDLE = "Idle"
    RECORDING = "Recording"
    PROCESSING = "Processing"

class DictationMode(str, Enum):
    SMART = "smart"
    LITERAL = "literal"

@dataclass(frozen=True)
class GroqModelSettings:
    base_url: str = "https://api.groq.com/openai/v1"
    transcription_model: str = "whisper-large-v3-turbo"
    # Instant 8B is much faster than gpt-oss-120b for cleanup; quality is enough
    # for punctuation / light Hinglish cleanup.
    cleanup_model: str = "llama-3.1-8b-instant"
    # Fast structured parse; falls back to cleanup_model when unset.
    parse_model: str | None = None
    vision_model: str = "meta-llama/llama-4-scout-17b-16e-instruct"
    response_format: str = "verbose_json"
    max_completion_tokens: int = 4096

@dataclass(frozen=True)
class AudioResult:
    path: Path
    duration_seconds: float

class AudioRecorder(Protocol):
    def start(self) -> AudioResult: ...
    def stop(self) -> AudioResult: ...

class KeyStore(Protocol):
    def get(self) -> str | None: ...
    def set(self, value: str) -> None: ...
    def remove(self) -> None: ...

class Notifier(Protocol):
    def notify(self, category: str, message: str) -> None: ...

class TextDelivery(Protocol):
    def deliver(self, text: str) -> str: ...

class HistoryStore(Protocol):
    def insert(self, **values: object) -> int: ...
