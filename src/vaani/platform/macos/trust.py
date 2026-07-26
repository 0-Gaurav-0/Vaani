"""macOS Accessibility / Input Monitoring trust helpers for global hotkeys."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def python_paths() -> list[str]:
    """Binaries macOS TCC may attribute the hotkey listener to."""
    exe = Path(sys.executable).resolve()
    paths = [str(exe)]
    # uv venv python is usually a symlink into the uv-managed CPython tree.
    try:
        venv_python = Path(sys.prefix) / "bin" / "python3"
        if venv_python.exists():
            paths.append(str(venv_python.resolve()))
    except OSError:
        pass
    # De-dupe, preserve order
    seen: set[str] = set()
    out: list[str] = []
    for path in paths:
        if path not in seen:
            seen.add(path)
            out.append(path)
    return out


def is_trusted() -> bool | None:
    """Return True/False when AX APIs are available, else None."""
    try:
        from ApplicationServices import AXIsProcessTrusted

        return bool(AXIsProcessTrusted())
    except Exception:
        return None


def request_trust_prompt() -> bool | None:
    """Show the system Accessibility prompt when possible."""
    try:
        from ApplicationServices import AXIsProcessTrustedWithOptions
        from Foundation import NSDictionary

        options = NSDictionary.dictionaryWithObject_forKey_(
            True, "AXTrustedCheckOptionPrompt"
        )
        return bool(AXIsProcessTrustedWithOptions(options))
    except Exception:
        # Fallback constant used by some pyobjc builds
        try:
            from ApplicationServices import AXIsProcessTrustedWithOptions
            from CoreFoundation import CFDictionaryCreate

            keys = ["AXTrustedCheckOptionPrompt"]
            values = [True]
            options = CFDictionaryCreate(None, keys, values, 1, None, None)
            return bool(AXIsProcessTrustedWithOptions(options))
        except Exception:
            return is_trusted()


def open_privacy_panes() -> None:
    """Best-effort open System Settings privacy panes."""
    urls = (
        "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
        "x-apple.systempreferences:com.apple.preference.security?Privacy_ListenEvent",
    )
    for url in urls:
        try:
            subprocess.run(["open", url], check=False, capture_output=True)
        except OSError:
            pass


def trust_help_text() -> str:
    paths = "\n".join(f"  - {p}" for p in python_paths())
    parent = os.environ.get("TERM_PROGRAM") or os.environ.get("__CFBundleIdentifier") or "Cursor / Terminal"
    return f"""macOS blocked Vaani global hotkeys (process not trusted).

Grant BOTH of these for the SAME session that runs `python -m vaani`:

1) System Settings → Privacy & Security → Accessibility
2) System Settings → Privacy & Security → Input Monitoring

Add / enable:
  - Cursor (or Terminal.app if you start Vaani there)
  - The Python binary itself:
{paths}

Then fully quit Cursor (Cmd+Q) and reopen, or run Vaani from Terminal.app:

  cd {Path.cwd()}
  source .venv/bin/activate
  python -m vaani

Launcher hint: {parent}
"""


def ensure_input_trust(*, prompt: bool = True, open_settings: bool = True) -> bool:
    """
    Ensure Accessibility trust for hotkeys.

    Returns True when trusted (or when the AX API is unavailable and we cannot
    tell). Returns False when explicitly untrusted.
    """
    trusted = request_trust_prompt() if prompt else is_trusted()
    if trusted is False and open_settings:
        open_privacy_panes()
    return trusted is not False
