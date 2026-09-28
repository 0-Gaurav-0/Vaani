#!/usr/bin/env python3
"""Show a clickable GNOME notification; on Open, run hermes-desktop with a URL.

Uses system PyGObject (Vaani's venv often lacks ``gi``). Invoked as:

  vaani-notify-helper.py TITLE BODY [SESSION_ID]
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import threading
from pathlib import Path


def open_session(session_id: str | None) -> None:
    desktop = shutil.which("hermes-desktop") or str(
        Path.home() / ".local" / "bin" / "hermes-desktop"
    )
    cmds: list[list[str]] = []
    if session_id:
        url = f"hermes://session/{session_id}"
        cmds.append([desktop, url])
        if shutil.which("xdg-open"):
            cmds.append(["xdg-open", url])
    cmds.append([desktop])
    for cmd in cmds:
        exe = cmd[0]
        if not exe:
            continue
        exe_path = Path(exe).expanduser()
        if not (exe_path.exists() or shutil.which(exe) or shutil.which(exe_path.name)):
            continue
        try:
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return
        except Exception:
            continue


def main() -> int:
    if len(sys.argv) < 3:
        return 2
    title, body = sys.argv[1], sys.argv[2]
    session_id = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] != "-" else None
    try:
        import gi

        gi.require_version("Notify", "0.7")
        from gi.repository import GLib, Notify
    except Exception:
        # Last-ditch non-clickable toast.
        ns = shutil.which("notify-send")
        if ns:
            subprocess.Popen(
                [
                    ns,
                    "-a",
                    "Vaani",
                    "-h",
                    "string:desktop-entry:hermes-desktop",
                    title,
                    body,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        return 0

    Notify.init("Vaani")
    loop = GLib.MainLoop()
    note = Notify.Notification.new(title, body, "dialog-information")
    note.set_app_name("Vaani")
    note.set_timeout(12000)
    try:
        note.set_hint_string("desktop-entry", "hermes-desktop")
    except Exception:
        pass

    opened = {"done": False}

    def on_open(_n, _action, _data=None) -> None:
        if opened["done"]:
            return
        opened["done"] = True
        threading.Thread(target=open_session, args=(session_id,), daemon=True).start()
        GLib.timeout_add(200, loop.quit)

    note.add_action("default", "Open", on_open, None)
    note.add_action("open", "Open in Hermes", on_open, None)
    note.connect("closed", lambda *_: GLib.timeout_add(50, loop.quit))
    note.show()
    # Auto-quit so we don't leak helper processes forever.
    GLib.timeout_add_seconds(60, loop.quit)
    loop.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
