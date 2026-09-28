"""State-aware GNOME/X11 actions for four-finger vertical gestures."""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from typing import Any

from Xlib import X, Xatom
from Xlib.protocol import event


class GnomeShellOverview:
    _BASE_COMMAND = [
        "gdbus",
        "call",
        "--session",
        "--dest",
        "org.gnome.Shell",
        "--object-path",
        "/org/gnome/Shell",
        "--method",
    ]

    def __init__(self, runner: Any = subprocess.run):
        self.runner = runner

    def _call(self, *arguments: str) -> subprocess.CompletedProcess:
        return self.runner(
            [*self._BASE_COMMAND, *arguments],
            check=True,
            capture_output=True,
            text=True,
        )

    def is_active(self) -> bool:
        result = self._call(
            "org.freedesktop.DBus.Properties.Get",
            "org.gnome.Shell",
            "OverviewActive",
        )
        match = re.fullmatch(r"\(\s*<(true|false)>,?\s*\)\s*", result.stdout)
        if match is None:
            raise RuntimeError("invalid GNOME OverviewActive response")
        return match.group(1) == "true"

    def set_active(self, active: bool) -> None:
        self._call(
            "org.freedesktop.DBus.Properties.Set",
            "org.gnome.Shell",
            "OverviewActive",
            f"<{str(active).lower()}>",
        )


class X11ShowingDesktop:
    def __init__(self, display: Any):
        self.display = display
        self.root = display.screen().root
        self.atom = display.intern_atom("_NET_SHOWING_DESKTOP")

    def is_showing(self) -> bool:
        prop = self.root.get_full_property(self.atom, Xatom.CARDINAL)
        if prop is None or not len(prop.value):
            raise RuntimeError("_NET_SHOWING_DESKTOP is unavailable")
        return bool(int(prop.value[0]))

    def set_showing(self, showing: bool) -> None:
        message = event.ClientMessage(
            window=self.root,
            client_type=self.atom,
            data=(32, [int(showing), 0, 0, 0, 0]),
        )
        self.root.send_event(
            message,
            event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask,
        )
        self.display.sync()


class DesktopGestureController:
    def __init__(self, overview: Any, desktop: Any):
        self.overview = overview
        self.desktop = desktop

    def handle(self, direction: str) -> None:
        if direction not in {"up", "down"}:
            raise ValueError(f"unsupported gesture direction: {direction}")
        overview_active = self.overview.is_active()
        desktop_showing = self.desktop.is_showing()
        if direction == "up":
            if desktop_showing:
                self.desktop.set_showing(False)
            else:
                self.overview.set_active(not overview_active)
        elif overview_active:
            self.overview.set_active(False)
        else:
            self.desktop.set_showing(not desktop_showing)


def main(argv: list[str] | None = None, *, controller: Any | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("direction", choices=("up", "down"))
    args = parser.parse_args(argv)
    dpy = None
    try:
        if controller is None:
            from Xlib.display import Display

            dpy = Display()
            controller = DesktopGestureController(
                GnomeShellOverview(),
                X11ShowingDesktop(dpy),
            )
        controller.handle(args.direction)
        return 0
    except Exception as exc:
        print(
            f"desktop gesture failed: {type(exc).__name__}",
            file=sys.stderr,
            flush=True,
        )
        return 1
    finally:
        if dpy is not None:
            dpy.close()


if __name__ == "__main__":
    raise SystemExit(main())
