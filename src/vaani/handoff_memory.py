"""Remember recent Vaani→Hermes handoffs so follow-ups can resume the right session.

Auto-guessing among similar tasks is unreliable. Callers should:
- resume when there is exactly one recent handoff, or a unique keyword match;
- otherwise present a clarify picker (titles the human can recognize).
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_PATH = Path.home() / ".local" / "share" / "vaani" / "handoffs.jsonl"
_MAX_RECORDS = 30
_STOP = {
    "a",
    "an",
    "the",
    "to",
    "of",
    "on",
    "in",
    "for",
    "and",
    "or",
    "is",
    "are",
    "was",
    "what",
    "whats",
    "status",
    "update",
    "that",
    "this",
    "task",
    "tasks",
    "todo",
    "todos",
    "please",
    "continue",
    "follow",
    "session",
    "same",
    "also",
    "just",
    "me",
    "my",
    "all",
    "get",
    "give",
    "tell",
    "check",
    "look",
}


@dataclass(frozen=True)
class HandoffRecord:
    session_id: str
    task: str
    title: str
    started_at: float


def handoff_store_path() -> Path:
    return Path(
        __import__("os").environ.get("VAANI_HANDOFF_STORE", "") or _DEFAULT_PATH
    )


def _title_from_task(task: str) -> str:
    text = " ".join((task or "").split())
    if not text:
        return "Untitled handoff"
    if len(text) > 72:
        return text[:69] + "…"
    return text


def remember_handoff(
    session_id: str,
    task: str,
    *,
    path: Path | None = None,
    started_at: float | None = None,
) -> None:
    sid = (session_id or "").strip()
    if not sid:
        return
    store = path or handoff_store_path()
    store.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "session_id": sid,
        "task": " ".join((task or "").split()),
        "title": _title_from_task(task),
        "started_at": float(started_at if started_at is not None else time.time()),
    }
    existing = [
        r
        for r in list_recent_handoffs(
            limit=_MAX_RECORDS,
            path=store,
            max_age_hours=None,
            hermes_fallback=False,
        )
        if r.session_id != sid
    ]
    rows = [rec] + [
        {
            "session_id": r.session_id,
            "task": r.task,
            "title": r.title,
            "started_at": r.started_at,
        }
        for r in existing
    ]
    tmp = store.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for row in rows[:_MAX_RECORDS]:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    tmp.replace(store)


def _task_from_hermes_user_content(content: str) -> str:
    text = content or ""
    marker = "## User request"
    if marker in text:
        text = text.split(marker, 1)[1]
    text = re.sub(r"^\[Vaani context\][^\n]*\n+", "", text.strip())
    return _title_from_task(text)


def list_hermes_vani_sessions(
    *,
    limit: int = 8,
    max_age_hours: float | None = 72.0,
    db_path: Path | None = None,
) -> list[HandoffRecord]:
    """Fallback: recent Hermes CLI sessions under the Vaani agent cwd."""
    db = db_path or (Path.home() / ".hermes" / "state.db")
    if not db.is_file():
        return []
    cutoff = 0.0
    if max_age_hours is not None:
        cutoff = time.time() - (max_age_hours * 3600.0)
    try:
        import sqlite3

        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=1.0)
        try:
            rows = con.execute(
                "SELECT id, title, started_at, cwd FROM sessions "
                "WHERE source = 'cli' AND (cwd LIKE '%/vani-task' OR cwd LIKE '%vaani-agent%') "
                "AND (? <= 0 OR started_at >= ?) "
                "ORDER BY started_at DESC LIMIT ?",
                (cutoff, cutoff, limit),
            ).fetchall()
            out: list[HandoffRecord] = []
            for sid, title, started, _cwd in rows:
                task = str(title or "").strip()
                if not task:
                    msg = con.execute(
                        "SELECT content FROM messages "
                        "WHERE session_id = ? AND role = 'user' "
                        "ORDER BY id ASC LIMIT 1",
                        (sid,),
                    ).fetchone()
                    task = _task_from_hermes_user_content(msg[0] if msg else "")
                out.append(
                    HandoffRecord(
                        session_id=str(sid),
                        task=task,
                        title=_title_from_task(task),
                        started_at=float(started or 0.0),
                    )
                )
            return out
        finally:
            con.close()
    except Exception:
        return []


def list_recent_handoffs(
    *,
    limit: int = 8,
    path: Path | None = None,
    max_age_hours: float | None = 72.0,
    hermes_fallback: bool = True,
) -> list[HandoffRecord]:
    store = path or handoff_store_path()
    cutoff = None
    if max_age_hours is not None:
        cutoff = time.time() - (max_age_hours * 3600.0)
    out: list[HandoffRecord] = []
    if store.is_file():
        try:
            lines = store.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            sid = str(raw.get("session_id") or "").strip()
            if not sid:
                continue
            started = float(raw.get("started_at") or 0.0)
            if cutoff is not None and started and started < cutoff:
                continue
            task = str(raw.get("task") or "")
            title = str(raw.get("title") or "") or _title_from_task(task)
            out.append(
                HandoffRecord(
                    session_id=sid, task=task, title=title, started_at=started
                )
            )
            if len(out) >= limit:
                return out
    if out:
        return out
    if hermes_fallback:
        return list_hermes_vani_sessions(limit=limit, max_age_hours=max_age_hours)
    return []


def _tokens(text: str) -> set[str]:
    parts = re.findall(r"[a-z0-9]{3,}", (text or "").casefold())
    return {p for p in parts if p not in _STOP}


def match_handoff(
    utterance: str, records: list[HandoffRecord]
) -> HandoffRecord | None:
    """Return a unique keyword match, or the only record when there is just one."""
    if not records:
        return None
    if len(records) == 1:
        return records[0]
    needle = _tokens(utterance)
    if not needle:
        return None
    scored: list[tuple[int, HandoffRecord]] = []
    for rec in records:
        hay = _tokens(f"{rec.title} {rec.task}")
        score = len(needle & hay)
        if score:
            scored.append((score, rec))
    if not scored:
        return None
    scored.sort(key=lambda item: (-item[0], -item[1].started_at))
    best_score, best = scored[0]
    if best_score < 1:
        return None
    if len(scored) == 1:
        return best
    second = scored[1][0]
    # Require a clear winner so similar Himanshu/Basecamp tasks don't collide.
    if best_score >= second + 1 and best_score >= 2:
        return best
    return None
