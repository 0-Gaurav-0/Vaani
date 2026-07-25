from vaani.history import HistoryEntry, HistoryStore
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
    assert c.execute("PRAGMA user_version").fetchone()[0] == 1

def test_timestamp_is_canonical_milliseconds(tmp_path):
    h = HistoryStore(tmp_path / "history.sqlite3")
    h.insert(raw_text="x", final_text="x", mode="literal")
    ts = h.list()[0]["created_at"]
    assert len(ts) == 24 and ts[-1] == "Z" and ts[19] == "."
