"""Linux focus probe: X11 via X11Probe; Wayland → UNSUPPORTED."""
from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from typing import Any

from vaani.context.focus import ActiveFocus, classify_role
from vaani.intent.schema import FocusInfo, Support


class LinuxFocusProbe:
    """X11 ``_NET_ACTIVE_WINDOW``; pure Wayland reports UNSUPPORTED."""

    def __init__(
        self,
        *,
        getenv: Callable[[str], str | None] | None = None,
        environ: Mapping[str, str] | None = None,
        open_probe: Callable[[], Any] | None = None,
        x11_probe: Any | None = None,
        window_meta: Callable[[Any, int], tuple[str | None, str | None]] | None = None,
    ) -> None:
        self._getenv = getenv or os.environ.get
        self._environ = environ
        self._open_probe = open_probe
        self._x11_probe = x11_probe
        self._window_meta = window_meta or _window_meta

    def probe(self) -> ActiveFocus:
        session = (self._lookup("XDG_SESSION_TYPE") or "").casefold()
        if session == "wayland":
            return ActiveFocus(
                info=None,
                role="other",
                support=Support.UNSUPPORTED,
                reason="focus unsupported on Wayland",
            )

        probe = self._x11_probe
        if probe is None:
            opener = self._open_probe
            if opener is None:
                try:
                    from vaani.x11 import open_probe as opener
                except Exception:
                    opener = None
            if opener is None:
                return ActiveFocus(
                    info=None,
                    role="other",
                    support=Support.DEGRADED,
                    reason="X11 probe unavailable",
                )
            try:
                probe = opener()
            except Exception as exc:  # noqa: BLE001
                return ActiveFocus(
                    info=None,
                    role="other",
                    support=Support.DEGRADED,
                    reason=f"X11 open failed: {type(exc).__name__}",
                )

        try:
            window_id = probe.active_window()
        except Exception as exc:  # noqa: BLE001
            return ActiveFocus(
                info=None,
                role="other",
                support=Support.DEGRADED,
                reason=f"X11 active window failed: {type(exc).__name__}",
            )
        if not window_id:
            return ActiveFocus(
                info=None,
                role="other",
                support=Support.DEGRADED,
                reason="no _NET_ACTIVE_WINDOW",
            )

        app_id, title = self._window_meta(probe, int(window_id))
        role = classify_role(app_id, title)
        return ActiveFocus(
            info=FocusInfo(app_id=app_id, window_title=title),
            role=role,
            support=Support.SUPPORTED,
        )

    def _lookup(self, key: str) -> str | None:
        if self._environ is not None:
            return self._environ.get(key)
        return self._getenv(key)


def _window_meta(probe: Any, window_id: int) -> tuple[str | None, str | None]:
    """Read WM_CLASS / _NET_WM_NAME from the active window (best-effort)."""
    display = getattr(probe, "display", None)
    if display is None:
        return None, None
    try:
        window = display.create_resource_object("window", window_id)
    except Exception:
        return None, None

    app_id: str | None = None
    title: str | None = None
    try:
        wm_class = window.get_wm_class()
        if wm_class:
            # (instance, class) — prefer class
            app_id = wm_class[-1] or wm_class[0]
    except Exception:
        pass
    try:
        atom = display.intern_atom("_NET_WM_NAME")
        prop = window.get_full_property(atom, 0)
        if prop and prop.value:
            raw = prop.value
            if isinstance(raw, bytes):
                title = raw.decode("utf-8", errors="replace")
            else:
                title = str(raw)
    except Exception:
        try:
            wm_name = window.get_wm_name()
            if wm_name:
                title = str(wm_name)
        except Exception:
            pass
    return app_id, title
