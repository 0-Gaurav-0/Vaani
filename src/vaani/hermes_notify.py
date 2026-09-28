"""Linux desktop notifications for Hermes agent handoffs + open-session helper.

Click opens ``hermes://session/<id>`` (Hermes desktop focuses that chat).
Notifications are shown via ``scripts/vaani-notify-helper.py`` under system
Python so GNOME action callbacks work even when Vaani's venv lacks ``gi``.
"""
from __future__ import annotations

import logging
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path

logger = logging.getLogger("vaani.hermes_notify")

_APP = "Vaani"
_DESKTOP_ENTRY = "hermes-desktop"


def _preview(text: str, *, limit: int = 140) -> str:
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "…"


def _helper_cmd() -> list[str] | None:
    """Prefer system Python (has gi); fall back to PATH python3."""
    candidates = (
        Path(__file__).resolve().parents[2] / "scripts" / "vaani-notify-helper.py",
        Path.home()
        / "Gaurav Projects/Vaani/03-REPOSITORIES/control/Vaani-main/scripts/vaani-notify-helper.py",
    )
    script = next((p for p in candidates if p.is_file()), None)
    if script is None:
        return None
    py = "/usr/bin/python3"
    if not Path(py).exists():
        py = shutil.which("python3") or "python3"
    return [py, str(script)]


def latest_vani_task_session(*, since: float | None = None) -> str | None:
    """Newest CLI session under the Vaani agent workspace (best-effort)."""
    db = Path.home() / ".hermes" / "state.db"
    if not db.is_file():
        return None
    cwd = str((Path.home() / "vaani-agent" / "vani-task").resolve())
    min_started = since if since is not None else time.time() - 3600
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=1.0)
        try:
            row = con.execute(
                "SELECT id FROM sessions "
                "WHERE source = 'cli' AND started_at >= ? "
                "AND (cwd = ? OR cwd LIKE '%/vani-task') "
                "ORDER BY started_at DESC LIMIT 1",
                (min_started, cwd),
            ).fetchone()
        finally:
            con.close()
    except Exception:
        return None
    return str(row[0]) if row else None


def open_hermes_session(session_id: str | None = None) -> None:
    """Open/focus Hermes on ``session_id`` via ``hermes://session/<id>``."""
    sid = session_id or latest_vani_task_session()
    desktop = shutil.which("hermes-desktop") or str(
        Path.home() / ".local" / "bin" / "hermes-desktop"
    )
    targets: list[list[str]] = []
    if sid:
        url = f"hermes://session/{sid}"
        targets.append([desktop, url])
        if shutil.which("xdg-open"):
            targets.append(["xdg-open", url])
    targets.append([desktop])
    for cmd in targets:
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
            logger.info(
                "event=hermes_open session=%s via=%s",
                sid or "-",
                exe,
            )
            for args in (
                ["wmctrl", "-xa", "Hermes"],
                ["wmctrl", "-a", "Hermes Agent"],
                ["wmctrl", "-a", "Hermes"],
            ):
                try:
                    subprocess.run(args, check=False, capture_output=True, timeout=2)
                except Exception:
                    pass
            return
        except Exception as exc:
            logger.debug("hermes open failed cmd=%s err=%s", cmd, type(exc).__name__)

def _notify_send_fallback(title: str, body: str) -> None:
    cmd = shutil.which("notify-send")
    if not cmd:
        return
    try:
        subprocess.Popen(
            [
                cmd,
                "-a",
                _APP,
                "-u",
                "normal",
                "-h",
                f"string:desktop-entry:{_DESKTOP_ENTRY}",
                title,
                body,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        pass


def notify_handoff(
    *,
    kind: str,
    task: str,
    session_id: str | None = None,
    session_ref: dict | None = None,
    detail: str | None = None,
) -> None:
    """Show a handoff notification. ``kind`` is started|done|failed|cancelled."""
    preview = _preview(task)
    if kind == "started":
        title = "Vaani → Hermes"
        body = (
            (preview or "Agent session started.")
            + "\nClick Open in Hermes for this session."
        )
    elif kind == "done":
        title = "Vaani agent · Done"
        body = preview
        if detail:
            body = f"{body}\n{_preview(detail, limit=100)}"
    elif kind == "failed":
        title = "Vaani agent · Failed"
        body = preview
        if detail:
            body = f"{body}\n{_preview(detail, limit=100)}"
    else:
        title = "Vaani agent · Cancelled"
        body = preview

    sid = None
    if isinstance(session_ref, dict) and session_ref.get("id"):
        sid = str(session_ref["id"])
    elif session_id:
        sid = session_id
    if not sid and kind in {"started", "done", "failed"}:
        sid = latest_vani_task_session(since=time.time() - 600)

    helper = _helper_cmd()
    if helper is None:
        logger.warning("event=notify_helper_missing")
        _notify_send_fallback(title, body)
        return
    try:
        subprocess.Popen(
            [*helper, title, body, sid or "-"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        logger.info(
            "event=notify_shown kind=%s session=%s title=%r",
            kind,
            sid or "-",
            title,
        )
    except Exception as exc:
        logger.warning("event=notify_failed detail=%s", type(exc).__name__)
        _notify_send_fallback(title, body)


def delete_hermes_session(session_id: str) -> bool:
    """Best-effort delete of a Hermes session Vaani started."""
    if not session_id:
        return False
    hermes = shutil.which("hermes")
    if not hermes:
        return False
    try:
        subprocess.run(
            [hermes, "sessions", "delete", session_id, "-y"],
            check=False,
            capture_output=True,
            timeout=15,
            text=True,
        )
        logger.info("event=hermes_session_deleted id=%s", session_id)
        return True
    except Exception as exc:
        logger.warning(
            "event=hermes_session_delete_failed id=%s detail=%s",
            session_id,
            type(exc).__name__,
        )
        return False
