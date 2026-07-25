"""Private, concurrent-safe SQLite transcript history."""
from __future__ import annotations
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
import shutil, sqlite3, threading
from typing import Any, Iterable

UTC_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"

@dataclass(frozen=True)
class HistoryEntry:
    raw_text: str
    final_text: str
    mode: str
    detected_language: str | None = None
    delivery_status: str | None = None
    cleanup_status: str | None = None
    duration_ms: int | None = None
    created_at: str | None = None

class HistoryStore:
    def __init__(self, path: str | Path, *, max_entries: int | None = None):
        self.path = Path(path); self.max_entries = max_entries
        self.disabled = False; self._lock = threading.RLock(); self._writer: sqlite3.Connection | None = None
        self.path.parent.mkdir(parents=True, exist_ok=True); self.path.parent.chmod(0o700)
        try:
            self.path.touch(mode=0o600, exist_ok=True); self.path.chmod(0o600)
            self._writer = self._connect(); self._migrate()
        except sqlite3.DatabaseError:
            self.disabled = True
            if self._writer: self._writer.close()
            self._writer = None
            # Preserve evidence and never destroy a user's history on corruption.
            try:
                backup = self.path.with_suffix(self.path.suffix + ".corrupt")
                if self.path.exists() and not backup.exists(): shutil.copy2(self.path, backup); backup.chmod(0o600)
            except OSError:
                pass

    def _connect(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=2.0, isolation_level=None, check_same_thread=False)
        c.execute("PRAGMA busy_timeout=2000"); c.execute("PRAGMA foreign_keys=ON")
        return c

    def _migrate(self) -> None:
        assert self._writer
        self._writer.execute("BEGIN EXCLUSIVE")
        version = self._writer.execute("PRAGMA user_version").fetchone()[0]
        self._writer.execute("""CREATE TABLE IF NOT EXISTS history (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          created_at TEXT NOT NULL, mode TEXT NOT NULL,
          detected_language TEXT, raw_text TEXT NOT NULL, final_text TEXT NOT NULL,
          delivery_status TEXT, cleanup_status TEXT, duration_ms INTEGER)""")
        cols = {r[1] for r in self._writer.execute("PRAGMA table_info(history)")}
        if "duration_ms" not in cols:
            self._writer.execute("ALTER TABLE history ADD COLUMN duration_ms INTEGER")
        if version < 1:
            self._writer.execute("PRAGMA user_version=1")
        self._writer.execute("CREATE INDEX IF NOT EXISTS idx_history_created ON history(created_at DESC, id DESC)")
        self._writer.execute("COMMIT")

    @staticmethod
    def _now() -> str:
        now = datetime.now(timezone.utc)
        # Canonical wire form uses milliseconds, never variable precision.
        now = now.replace(microsecond=(now.microsecond // 1000) * 1000)
        return now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

    def insert(self, entry: HistoryEntry | None = None, **values: Any) -> int | None:
        if self.disabled: return None
        if entry is not None: values = asdict(entry) | values
        created = values.get("created_at") or self._now()
        with self._lock:
            try:
                assert self._writer
                self._writer.execute("BEGIN IMMEDIATE")
                cur = self._writer.execute("INSERT INTO history(created_at,mode,detected_language,raw_text,final_text,delivery_status,cleanup_status,duration_ms) VALUES (?,?,?,?,?,?,?,?)",
                    (created, values.get("mode", "literal"), values.get("detected_language"), values.get("raw_text", ""), values.get("final_text", ""), values.get("delivery_status"), values.get("cleanup_status"), values.get("duration_ms")))
                if self.max_entries is not None:
                    self._writer.execute("DELETE FROM history WHERE id NOT IN (SELECT id FROM history ORDER BY created_at DESC,id DESC LIMIT ?)", (self.max_entries,))
                self._writer.execute("COMMIT")
                return int(cur.lastrowid)
            except sqlite3.DatabaseError:
                try: self._writer.execute("ROLLBACK")
                except Exception: pass
                self.disabled = True; return None

    def _read(self, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
        if self.disabled: return []
        try:
            c = self._connect()
            try:
                c.row_factory = sqlite3.Row
                return [dict(r) for r in c.execute(sql, args)]
            finally: c.close()
        except sqlite3.DatabaseError:
            return []

    def list(self, query: str = "") -> list[dict[str, Any]]:
        rows = self._read("SELECT * FROM history ORDER BY created_at DESC,id DESC")
        if query:
            q = query.casefold(); rows = [r for r in rows if q in r["raw_text"].casefold() or q in r["final_text"].casefold()]
        return rows
    search = list

    def delete(self, row_id: int) -> bool:
        if self.disabled: return False
        with self._lock:
            try: assert self._writer; cur = self._writer.execute("DELETE FROM history WHERE id=?", (row_id,)); return cur.rowcount > 0
            except sqlite3.DatabaseError: self.disabled = True; return False

    def clear(self) -> int:
        if self.disabled: return 0
        with self._lock:
            try: assert self._writer; cur = self._writer.execute("DELETE FROM history"); return cur.rowcount
            except sqlite3.DatabaseError: self.disabled = True; return 0

    clear_all = clear

    def export(self) -> list[dict[str, Any]]: return self.list()

    def close(self) -> None:
        with self._lock:
            if self._writer: self._writer.close(); self._writer = None
