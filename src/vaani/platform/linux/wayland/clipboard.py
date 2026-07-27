"""Wayland clipboard (wl-clipboard) and best-effort paste (wtype)."""
from __future__ import annotations

import shutil
import subprocess


class WlClipboard:
    """Clipboard access via wl-clipboard's wl-copy/wl-paste CLIs.

    Mirrors the xclip fallback in ``vaani.delivery.GtkClipboard`` — no GTK
    clipboard object exists in a headless Wayland backend, so this is the
    only path, not a fallback.
    """

    def set_text(self, text: str) -> None:
        subprocess.run(
            ["wl-copy"], input=text, text=True, check=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    def read_text(self) -> str | None:
        result = subprocess.run(
            ["wl-paste", "--no-newline"], text=True, capture_output=True, check=False,
        )
        return result.stdout if result.returncode == 0 else None


class WtypeUnavailable(RuntimeError):
    """Raised when wtype is missing or the compositor rejects synthetic input.

    Expected on GNOME/KDE Wayland, which don't implement the wlroots
    virtual-keyboard protocol wtype depends on; callers should treat this as
    a normal signal to fall back to clipboard-only delivery, not a bug.
    """


class WtypePaster:
    """Synthetic Ctrl+V via wtype (wlroots compositors: Sway, Hyprland, ...).

    No active-window/terminal detection is possible on Wayland (no portal
    exposes it to unprivileged clients), so this always sends plain Ctrl+V —
    unlike the X11 paster, it cannot special-case Ctrl+Shift+V for terminal
    emulators. Known gap, not a bug: pasting into a terminal via auto-paste
    on Wayland may not work even where wtype itself succeeds.
    """

    def __init__(self, *, binary: str = "wtype"):
        self._binary = binary

    def paste(self) -> None:
        if shutil.which(self._binary) is None:
            raise WtypeUnavailable(f"{self._binary} not found on PATH")
        try:
            subprocess.run(
                [self._binary, "-M", "ctrl", "-k", "v", "-m", "ctrl"],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except subprocess.CalledProcessError as exc:
            raise WtypeUnavailable(
                "wtype paste failed (compositor likely lacks virtual-keyboard support)"
            ) from exc
