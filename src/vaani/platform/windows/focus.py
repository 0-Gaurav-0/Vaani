"""Windows UI Automation / Win32 foreground-window focus probe."""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from vaani.context.focus import ActiveFocus, classify_role, project_root_from_document
from vaani.intent.schema import FocusInfo, Support


class WindowsFocusProbe:
    """Best-effort foreground window via Win32; optional UIA document path."""

    def __init__(
        self,
        *,
        win32: Any | None = None,
        uia_document: Callable[[], Path | None] | None = None,
    ) -> None:
        self._win32 = win32
        self._uia_document = uia_document

    def probe(self) -> ActiveFocus:
        title, app_id = self._foreground()
        if not title and not app_id:
            return ActiveFocus(
                info=None,
                role="other",
                support=Support.DEGRADED,
                reason="Windows foreground window unavailable",
            )
        document = None
        if self._uia_document is not None:
            try:
                document = self._uia_document()
            except Exception:
                document = None
        else:
            document = self._default_uia_document()
        role = classify_role(app_id, title)
        info = FocusInfo(app_id=app_id, window_title=title, document_path=document)
        project_root = (
            project_root_from_document(document) if role == "editor" else None
        )
        return ActiveFocus(
            info=info,
            role=role,
            project_root=project_root,
            cwd=None,
            support=Support.SUPPORTED,
        )

    def _foreground(self) -> tuple[str | None, str | None]:
        api = self._win32 if self._win32 is not None else _load_win32()
        if api is None:
            return None, None
        try:
            hwnd = api.user32.GetForegroundWindow()
            if not hwnd:
                return None, None
            length = api.user32.GetWindowTextLengthW(hwnd)
            buf = api.create_unicode_buffer(length + 1)
            api.user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value or None
            pid = api.wintypes.DWORD()
            api.user32.GetWindowThreadProcessId(hwnd, api.ctypes.byref(pid))
            app_id = _process_name(api, int(pid.value)) or title
            return title, app_id
        except Exception:
            return None, None

    def _default_uia_document(self) -> Path | None:
        # Optional: UI Automation for document path is environment-dependent.
        # Real UIA wiring can replace this; keep best-effort None for CI.
        return None


def _load_win32() -> Any | None:
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return None

    class _Api:
        ctypes = ctypes
        wintypes = wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        create_unicode_buffer = ctypes.create_unicode_buffer

    return _Api()


def _process_name(api: Any, pid: int) -> str | None:
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = api.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        size = api.wintypes.DWORD(260)
        buf = api.create_unicode_buffer(260)
        query = getattr(api.kernel32, "QueryFullProcessImageNameW", None)
        if query is None:
            return None
        if not query(handle, 0, buf, api.ctypes.byref(size)):
            return None
        path = Path(buf.value)
        return path.name or None
    except Exception:
        return None
    finally:
        api.kernel32.CloseHandle(handle)
