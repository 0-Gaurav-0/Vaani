"""Cross-platform adapter contracts for Vaani desktop I/O."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from ..types import AudioRecorder, KeyStore

if TYPE_CHECKING:
    from ..config import Settings
    from ..controller import Controller
    from ..delivery import DeliveryStatus
    from ..intent.schema import Result, ScreenFrame
    from ..types import AudioResult


@dataclass(frozen=True)
class ProcInfo:
    """A process discovered for port/name lookups (T2.4)."""

    pid: int
    name: str
    uid: int | None = None
    detail: str = ""


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


@runtime_checkable
class SystemControl(Protocol):
    """OS volume / DND / lock / network / trash / process surfaces (T1.2 + T2.4)."""

    def volume_set(self, pct: int) -> Result: ...
    def mute(self, enabled: bool) -> Result: ...
    def dnd(self, enabled: bool) -> Result: ...
    def lock(self) -> Result: ...
    def display_sleep(self) -> Result: ...
    def wifi(self, enabled: bool) -> Result: ...
    def dns_flush(self) -> Result: ...
    def trash_empty(self) -> Result: ...
    def local_ip(self) -> str: ...

    def list_listeners(self, port: int) -> tuple[ProcInfo, ...]: ...
    def list_named(self, name: str) -> tuple[ProcInfo, ...]: ...
    def kill_pids(
        self,
        pids: Sequence[int],
        *,
        signal: str = "term",
    ) -> Result: ...
    def open_process_monitor(self) -> Result: ...


@runtime_checkable
class WindowControl(Protocol):
    """Focus / tile / hide-others window management (T5.1)."""

    def focus(self, target: str) -> Result: ...
    def tile(self, side: str) -> Result: ...
    def hide_others(self) -> Result: ...


@runtime_checkable
class InputSynth(Protocol):
    """Rung-7 keystroke injection into the focused surface (T5.2)."""

    def type_text(self, text: str) -> Result: ...
    def hotkey(self, *keys: str) -> Result: ...


@runtime_checkable
class TerminalOpener(Protocol):
    """Open a terminal application at a working directory (TERM-SESS-01)."""

    def open(self, cwd: str | Path) -> Result: ...


@runtime_checkable
class ScreenCapture(Protocol):
    """In-memory display capture for guide mode (T6.1). Never writes to disk."""

    def capture(self, *, display_index: int = 0) -> ScreenFrame: ...


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
    # Optional verb-stack surfaces — absence is the capability signal (§2.3).
    system: SystemControl | None = None
    window: WindowControl | None = None
    input: InputSynth | None = None
    terminal: TerminalOpener | None = None
    screen: ScreenCapture | None = None
