"""macOS Accessibility (AX) / System Events focus probe."""
from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

from vaani.context.focus import ActiveFocus, classify_role, project_root_from_document
from vaani.intent.schema import FocusInfo, Support

_FRONTMOST_SCRIPT = (
    'tell application "System Events" to '
    "get {name, bundle identifier} of first application process whose frontmost is true"
)

_DOCUMENT_SCRIPT = (
    'tell application "System Events"\n'
    "  set frontApp to first application process whose frontmost is true\n"
    '  try\n'
    "    set docPath to value of attribute \"AXDocument\" of first window of frontApp\n"
    "    return docPath\n"
    "  end try\n"
    "  return \"\"\n"
    "end tell"
)


def _default_runner(args: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, **kwargs)


class MacOSFocusProbe:
    """Best-effort frontmost app + AX document path via osascript."""

    def __init__(
        self,
        *,
        runner: Callable[..., subprocess.CompletedProcess[str]] = _default_runner,
    ) -> None:
        self._runner = runner

    def probe(self) -> ActiveFocus:
        app_name, bundle_id = self._frontmost()
        if not app_name and not bundle_id:
            return ActiveFocus(
                info=None,
                role="other",
                support=Support.DEGRADED,
                reason="macOS frontmost app unavailable",
            )
        app_id = bundle_id or app_name
        document = self._document_path()
        role = classify_role(app_id, app_name)
        info = FocusInfo(
            app_id=app_id,
            window_title=app_name,
            document_path=document,
        )
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

    def _frontmost(self) -> tuple[str | None, str | None]:
        try:
            result = self._runner(
                ["osascript", "-e", _FRONTMOST_SCRIPT],
                check=False,
                capture_output=True,
                text=True,
                timeout=2.0,
            )
        except Exception:
            return None, None
        if getattr(result, "returncode", 1) != 0:
            return None, None
        stdout = (getattr(result, "stdout", None) or "").strip()
        if not stdout:
            return None, None
        parts = [part.strip() for part in stdout.split(",", 1)]
        if len(parts) == 1:
            return parts[0] or None, None
        return parts[0] or None, parts[1] or None

    def _document_path(self) -> Path | None:
        try:
            result = self._runner(
                ["osascript", "-e", _DOCUMENT_SCRIPT],
                check=False,
                capture_output=True,
                text=True,
                timeout=2.0,
            )
        except Exception:
            return None
        if getattr(result, "returncode", 1) != 0:
            return None
        raw = (getattr(result, "stdout", None) or "").strip()
        if not raw or raw in {"missing value", "null"}:
            return None
        # AXDocument often looks like file:///path
        if raw.startswith("file://"):
            raw = raw.removeprefix("file://")
        path = Path(raw)
        return path if path.exists() else path
