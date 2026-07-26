"""Floating recording pill for Linux (shared tkinter stadium).

Uses the same bottom pill as Windows: grey X, live waveform, white check.
While processing, the pill stays up with pulsing dots.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> int:
    from ...indicator_protocol import resolve_control_path, resolve_phase_path
    from ...indicator_tk import run_pill

    amp = Path(os.environ.get("VAANI_AMPLITUDE_PATH", "/tmp/vaani-amplitude"))
    control = resolve_control_path()
    phase = resolve_phase_path()
    xdg = os.environ.get("XDG_CONFIG_HOME")
    config = Path(xdg) if xdg else Path.home() / ".config"
    pos = config / "vaani" / "indicator.json"
    return run_pill(
        amplitude_path=amp,
        control_path=control,
        position_path=pos,
        phase_path=phase,
    )


if __name__ == "__main__":
    raise SystemExit(main())
