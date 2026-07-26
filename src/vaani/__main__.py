"""``python -m vaani`` entry — dispatches through the CLI layer."""
from __future__ import annotations

from .cli import main, manual_record

__all__ = ["main", "manual_record"]


if __name__ == "__main__":
    raise SystemExit(main())
