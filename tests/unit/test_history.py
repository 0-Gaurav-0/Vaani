from vaani.history import HistoryEntry, HistoryStore
import json
import sqlite3


def test_history_search_order_and_metadata_only(tmp_path):
    h = HistoryStore(tmp_path / "history.sqlite3")
    h.insert(HistoryEntry("Raw", "Final", "smart", "en", "pasted"))
    h.insert(raw_text="second", final_text="Other", mode="literal")
    assert [r["final_text"] for r in h.search("RAW")] == ["Final"]
    assert set(h.list()[0]) >= {"id", "created_at", "raw_text", "final_text", "mode"}
    assert "audio" not in h.list()[0] and "key" not in h.list()[0]


def test_history_retention_delete_clear(tmp_path):
    h = HistoryStore(tmp_path / "history.sqlite3", max_entries=2)
    a = h.insert(raw_text="a", final_text="a", mode="literal")
    h.insert(raw_text="b", final_text="b", mode="literal")
    h.insert(raw_text="c", final_text="c", mode="literal")
    assert len(h.list()) == 2
    assert not h.delete(a)
    assert h.clear() == 2


def test_history_migrates_old_schema_and_version(tmp_path):
    p = tmp_path / "history.sqlite3"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE history (id INTEGER PRIMARY KEY, created_at TEXT, mode TEXT, detected_language TEXT, raw_text TEXT, final_text TEXT, delivery_status TEXT, cleanup_status TEXT)")
    c.commit(); c.close()
    h = HistoryStore(p)
    assert h.insert(raw_text="x", final_text="x", mode="literal", duration_ms=123)
    row = h.list()[0]
    assert row["duration_ms"] == 123
    c = sqlite3.connect(p)
    assert c.execute("PRAGMA user_version").fetchone()[0] == 2


def test_history_migrates_v1_rows_to_v2_columns(tmp_path):
    p = tmp_path / "history.sqlite3"
    c = sqlite3.connect(p)
    c.execute(
        """CREATE TABLE history (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          created_at TEXT NOT NULL, mode TEXT NOT NULL,
          detected_language TEXT, raw_text TEXT NOT NULL, final_text TEXT NOT NULL,
          delivery_status TEXT, cleanup_status TEXT, duration_ms INTEGER)"""
    )
    c.execute(
        """INSERT INTO history(
            created_at, mode, detected_language, raw_text, final_text,
            delivery_status, cleanup_status, duration_ms
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        ("2026-01-01T00:00:00.000Z", "assistant", None, "open Terminal", "Opened Terminal.",
         "displayed", "app_action", 42),
    )
    c.execute("PRAGMA user_version=1")
    c.commit()
    c.close()

    h = HistoryStore(p)
    c = sqlite3.connect(p)
    assert c.execute("PRAGMA user_version").fetchone()[0] == 2
    cols = {r[1] for r in c.execute("PRAGMA table_info(history)")}
    for name in (
        "verb", "rung", "risk", "status", "confirmed_by",
        "workspace", "workspace_source", "brain", "evidence", "slots",
    ):
        assert name in cols
    c.close()

    legacy = h.list()[0]
    assert legacy["raw_text"] == "open Terminal"
    assert legacy["cleanup_status"] == "app_action"
    assert legacy["duration_ms"] == 42
    assert legacy["verb"] is None

    assert h.insert(
        raw_text="run the tests",
        final_text="started",
        mode="assistant",
        cleanup_status="skipped",
        verb="project.test.run",
        rung=3,
        risk="R1",
        status="ok",
        confirmed_by=None,
        workspace="/tmp/ws",
        workspace_source="git_root",
        brain=None,
        evidence=("pytest", "-q"),
        slots={"scope": "unit"},
    )
    rows = h.list()
    assert len(rows) == 2
    fresh = rows[0]
    assert fresh["verb"] == "project.test.run"
    assert fresh["rung"] == 3
    assert fresh["risk"] == "R1"
    assert fresh["status"] == "ok"
    assert fresh["workspace"] == "/tmp/ws"
    assert fresh["workspace_source"] == "git_root"
    assert json.loads(fresh["evidence"]) == ["pytest", "-q"]
    assert json.loads(fresh["slots"]) == {"scope": "unit"}
    # Old row still readable alongside new columns.
    older = rows[1]
    assert older["cleanup_status"] == "app_action"
    assert older["verb"] is None


def test_history_redacts_secret_shaped_slots_before_storage(tmp_path):
    h = HistoryStore(tmp_path / "history.sqlite3")
    secret = "supersecret-value"
    h.insert(
        raw_text="export key",
        final_text="ok",
        mode="assistant",
        verb="terminal.env.export",
        status="refused",
        evidence=("token=" + secret, "plain"),
        slots={"api_key": "api_key=" + secret, "name": "HOME"},
    )
    row = h.list()[0]
    blob = json.dumps(row)
    assert secret not in blob
    assert "supersecret" not in blob
    slots = json.loads(row["slots"])
    assert slots["api_key"] == "api_key=[REDACTED]"
    assert slots["name"] == "HOME"
    evidence = json.loads(row["evidence"])
    assert evidence[0] == "token=[REDACTED]"
    assert evidence[1] == "plain"


def test_timestamp_is_canonical_milliseconds(tmp_path):
    h = HistoryStore(tmp_path / "history.sqlite3")
    h.insert(raw_text="x", final_text="x", mode="literal")
    ts = h.list()[0]["created_at"]
    assert len(ts) == 24 and ts[-1] == "Z" and ts[19] == "."
