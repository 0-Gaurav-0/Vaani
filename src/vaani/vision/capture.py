"""Capture errors shared by platform screen-capture leaves."""
from __future__ import annotations


class ScreenCaptureError(RuntimeError):
    """Raised when an in-memory display capture cannot be produced."""
