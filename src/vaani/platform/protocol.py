"""Cross-platform adapter contracts for Vaani desktop I/O."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from ..types import AudioRecorder, KeyStore

if TYPE_CHECKING:
    from ..config import Settings
    from ..controller import Controller
    from ..delivery import DeliveryStatus
    from ..types import AudioResult


class PlatformId(str, Enum):
    LINUX = "linux"
    MACOS = "macos"
    WINDOWS = "windows"


class UnsupportedPlatform(RuntimeError):
    """Raised when Vaani cannot build a bundle for the current OS."""


@dataclass(frozen=True)
class FocusSnapshot:
    """Opaque focus token used to detect target changes before paste."""

    token: str


@dataclass(frozen=True)
class AppTarget:
    name: str
    executables: tuple[str, ...]
    arguments: tuple[str, ...] = ()
    # macOS: application name for `open -a`; Windows: display / shell name.
    native_name: str | None = None


@runtime_checkable
class HotkeyService(Protocol):
    def register(self) -> None: ...
    def unregister(self) -> None: ...


@runtime_checkable
class TargetProbe(Protocol):
    def snapshot(self) -> FocusSnapshot | Any | None: ...
    def unchanged(self, before: FocusSnapshot | Any) -> bool: ...


@runtime_checkable
class TextDelivery(Protocol):
    def deliver(self, text: str, *, snapshot: Any | None = None) -> Any: ...


@runtime_checkable
class AppLauncher(Protocol):
    def resolve(self, command: str) -> Any | None: ...
    def launch(self, target: Any) -> str: ...


@runtime_checkable
class BrowserLauncher(Protocol):
    def open(self, url: str, *, prefer: str | None = None) -> str: ...


@runtime_checkable
class FeedbackService(Protocol):
    def play(self, cue: str) -> bool: ...
    def notify(self, category: str, message: str = "") -> None: ...


@dataclass
class PlatformBundle:
    """Concrete OS wiring assembled by ``build_platform``."""

    id: PlatformId
    settings: Any
    recorder: AudioRecorder
    hotkeys: HotkeyService
    target: TargetProbe
    delivery: TextDelivery
    apps: AppLauncher
    browser: BrowserLauncher
    feedback: FeedbackService
    key_store: KeyStore
    run: Any  # Callable[[Controller], int]
