"""Best-effort remote audit into HistoryStore (T8.1 columns optional)."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Mapping

def _table_columns(db_path: Path) -> set[str]:
    try:
        conn = sqlite3.connect(str(db_path), timeout=1.0)
        try:
            rows = conn.execute("PRAGMA table_info(history)").fetchall()
            return {str(r[1]) for r in rows}
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        return set()


def audit_remote(
    history: Any,
    *,
    raw_text: str,
    final_text: str,
    caller: str,
    verb: str = "",
    rung: int | None = None,
    risk: str | None = None,
    status: str | None = None,
    confirmed_by: str | None = None,
    workspace: str | None = None,
    workspace_source: str | None = None,
    brain: str | None = None,
    evidence: Mapping[str, Any] | str | tuple[str, ...] | list[str] | None = None,
    duration_ms: int | None = None,
) -> int | None:
    """Insert a remote-attributed history row.

    Always writes base v1 fields. When history v2 columns exist, fills them.
    When they do not, encodes the caller in ``cleanup_status`` as
    ``remote:<caller>`` (and ``confirmed_by`` when provided).
    """
    if history is None:
        return None

    caller_tag = f"remote:{caller}"
    if confirmed_by:
        caller_tag = f"remote:{caller};confirmed_by={confirmed_by}"

    base: dict[str, Any] = {
        "raw_text": raw_text,
        "final_text": final_text,
        "mode": "remote",
        "delivery_status": status or "received",
        "cleanup_status": caller_tag,
        "duration_ms": duration_ms,
    }

    db_path = getattr(history, "path", None)
    cols = _table_columns(Path(db_path)) if db_path is not None else set()
    extras: dict[str, Any] = {}
    if "verb" in cols and verb:
        extras["verb"] = verb
    if "rung" in cols and rung is not None:
        extras["rung"] = rung
    if "risk" in cols and risk is not None:
        extras["risk"] = risk
    if "status" in cols and status is not None:
        extras["status"] = status
    if "confirmed_by" in cols:
        extras["confirmed_by"] = confirmed_by or caller
    if "workspace" in cols and workspace is not None:
        extras["workspace"] = workspace
    if "workspace_source" in cols and workspace_source is not None:
        extras["workspace_source"] = workspace_source
    if "brain" in cols and brain is not None:
        extras["brain"] = brain
    if "evidence" in cols and evidence is not None:
        if isinstance(evidence, (tuple, list)):
            extras["evidence"] = " ".join(str(x) for x in evidence)
        elif isinstance(evidence, Mapping):
            extras["evidence"] = str(dict(evidence))
        else:
            extras["evidence"] = str(evidence)

    if extras and hasattr(history, "insert_extended"):
        return history.insert_extended(**(base | extras))

    # Prefer a dynamic write when optional columns exist on the store's DB.
    if extras and db_path is not None and not getattr(history, "disabled", False):
        row_id = _insert_with_extras(history, base, extras, cols)
        if row_id is not None:
            return row_id

    insert = getattr(history, "insert", None)
    if not callable(insert):
        return None
    return insert(**base)


def _insert_with_extras(
    history: Any,
    base: dict[str, Any],
    extras: dict[str, Any],
    cols: set[str],
) -> int | None:
    """Write base + available extras inside the store's lock when possible."""
    lock = getattr(history, "_lock", None)
    writer = getattr(history, "_writer", None)
    if writer is None:
        return None

    allowed = {k: v for k, v in {**base, **extras}.items() if k in cols or k in {
        "created_at",
        "mode",
        "detected_language",
        "raw_text",
        "final_text",
        "delivery_status",
        "cleanup_status",
        "duration_ms",
    }}
    # Always require core columns.
    for required in ("raw_text", "final_text", "mode"):
        if required not in allowed:
            allowed[required] = base.get(required, "")

    now_fn = getattr(type(history), "_now", None)
    created = allowed.get("created_at")
    if not created and callable(now_fn):
        created = now_fn()
    allowed["created_at"] = created

    columns = [c for c in allowed if c in cols or c == "created_at"]
    # Filter to actual table columns.
    columns = [c for c in columns if c in cols]
    if not columns:
        return None
    placeholders = ",".join("?" for _ in columns)
    col_sql = ",".join(columns)
    values = tuple(allowed.get(c) for c in columns)

    def _write() -> int | None:
        try:
            writer.execute("BEGIN IMMEDIATE")
            cur = writer.execute(
                f"INSERT INTO history({col_sql}) VALUES ({placeholders})",
                values,
            )
            writer.execute("COMMIT")
            return int(cur.lastrowid)
        except sqlite3.DatabaseError:
            try:
                writer.execute("ROLLBACK")
            except Exception:
                pass
            return None

    if lock is not None:
        with lock:
            return _write()
    return _write()
