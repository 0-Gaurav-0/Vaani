"""Floating recording pill for Windows (tkinter)."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> int:
    from ...indicator_protocol import (
        resolve_control_path,
        resolve_pending_path,
        resolve_phase_path,
    )
    from ...indicator_tk import run_pill

    amp = Path(os.environ.get("VAANI_AMPLITUDE_PATH", "/tmp/vaani-amplitude"))
    control = resolve_control_path()
    phase = resolve_phase_path()
    pending = resolve_pending_path()
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    pos = local / "Vaani" / "indicator.json"
    return run_pill(
        amplitude_path=amp,
        control_path=control,
        position_path=pos,
        phase_path=phase,
        pending_path=pending,
    )


if __name__ == "__main__":
    raise SystemExit(main())
