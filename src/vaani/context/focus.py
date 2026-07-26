"""Shared focus API with injectable OS probes (fakes in tests)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from vaani.intent.schema import FocusInfo, Support
from vaani.platform.protocol import PlatformId

_EDITOR_MARKERS = frozenset(
    {
        ".git",
        "package.json",
        "pyproject.toml",
        "Cargo.toml",
        "go.mod",
        "Makefile",
        "vaani.toml",
    }
)

_EDITOR_NAMES = frozenset(
    {
        "code",
        "code - insiders",
        "cursor",
        "visual studio code",
        "sublime text",
        "sublime_text",
        "nvim",
        "neovim",
        "vim",
        "emacs",
        "zed",
        "idea",
        "pycharm",
        "webstorm",
        "goland",
        "rider",
        "android studio",
        "textedit",
        "notepad",
        "notepad++",
    }
)

_TERMINAL_NAMES = frozenset(
    {
        "terminal",
        "iterm2",
        "iterm",
        "kitty",
        "alacritty",
        "wezterm",
        "ghostty",
        "warp",
        "hyper",
        "gnome-terminal",
        "gnome terminal",
        "konsole",
        "xfce4-terminal",
        "mate-terminal",
        "tilix",
        "terminator",
        "windows terminal",
        "windowsterminal",
        "wt",
        "powershell",
        "pwsh",
        "cmd",
        "cmd.exe",
        "conhost",
        "windows console host",
    }
)


@dataclass(frozen=True)
class ActiveFocus:
    """Rich focus probe result used by workspace precedence + Context.focus."""

    info: FocusInfo | None = None
    role: str = "other"  # "editor" | "terminal" | "other"
    project_root: Path | None = None
    cwd: Path | None = None
    support: Support = Support.SUPPORTED
    reason: str = ""


@runtime_checkable
class FocusProbe(Protocol):
    def probe(self) -> ActiveFocus: ...


@dataclass(frozen=True)
class FakeFocusProbe:
    """Test double — returns a fixed ActiveFocus."""

    active: ActiveFocus

    def probe(self) -> ActiveFocus:
        return self.active


class NullFocusProbe:
    """Probe that reports no focus (still SUPPORTED — nothing focused)."""

    def probe(self) -> ActiveFocus:
        return ActiveFocus(info=None, role="other")


def classify_role(app_id: str | None, window_title: str | None = None) -> str:
    blob = " ".join(part for part in (app_id, window_title) if part).casefold()
    if not blob:
        return "other"
    # Prefer terminal match when both could apply (e.g. "Code" in a terminal title).
    for name in _TERMINAL_NAMES:
        if name in blob:
            return "terminal"
    for name in _EDITOR_NAMES:
        if name in blob:
            return "editor"
    return "other"


def project_root_from_document(document_path: Path | None) -> Path | None:
    """Walk parents from a document path looking for project markers."""
    if document_path is None:
        return None
    try:
        current = document_path.expanduser().resolve()
    except OSError:
        return None
    if current.is_file():
        current = current.parent
    for parent in (current, *current.parents):
        for marker in _EDITOR_MARKERS:
            if (parent / marker).exists():
                return parent
        # Stop at filesystem root.
        if parent.parent == parent:
            break
    return current if current.is_dir() else None


def enrich(active: ActiveFocus) -> ActiveFocus:
    """Fill role / project_root defaults from FocusInfo when missing."""
    info = active.info
    if info is None:
        return active
    role = active.role if active.role != "other" else classify_role(
        info.app_id, info.window_title
    )
    project_root = active.project_root
    if project_root is None and role == "editor":
        project_root = project_root_from_document(info.document_path)
    return ActiveFocus(
        info=info,
        role=role,
        project_root=project_root,
        cwd=active.cwd,
        support=active.support,
        reason=active.reason,
    )


def probe_for_platform(platform: PlatformId) -> FocusProbe:
    """Return the default OS focus probe (best-effort; may be UNSUPPORTED)."""
    if platform is PlatformId.MACOS:
        from vaani.platform.macos.focus import MacOSFocusProbe

        return MacOSFocusProbe()
    if platform is PlatformId.WINDOWS:
        from vaani.platform.windows.focus import WindowsFocusProbe

        return WindowsFocusProbe()
    from vaani.platform.linux.focus import LinuxFocusProbe

    return LinuxFocusProbe()


def resolve_focus(probe: FocusProbe | None) -> ActiveFocus:
    if probe is None:
        return ActiveFocus(info=None, role="other")
    try:
        return enrich(probe.probe())
    except Exception as exc:  # noqa: BLE001 — best-effort context
        return ActiveFocus(
            info=None,
            role="other",
            support=Support.DEGRADED,
            reason=f"focus probe failed: {type(exc).__name__}",
        )
