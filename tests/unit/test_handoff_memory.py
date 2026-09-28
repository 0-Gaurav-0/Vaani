import time

from vaani.handoff_memory import (
    HandoffRecord,
    list_recent_handoffs,
    match_handoff,
    remember_handoff,
)


def test_remember_and_list(tmp_path):
    store = tmp_path / "handoffs.jsonl"
    remember_handoff("s1", "Himanshu Basecamp report", path=store, started_at=100.0)
    remember_handoff("s2", "Reconciliation mailbox status", path=store, started_at=200.0)
    rows = list_recent_handoffs(limit=5, path=store, max_age_hours=None)
    assert [r.session_id for r in rows] == ["s2", "s1"]
    assert "Himanshu" in rows[1].title


def test_match_unique_keyword():
    records = [
        HandoffRecord("a", "Himanshu initiatives report", "Himanshu initiatives", 3.0),
        HandoffRecord("b", "Reconciliation mailbox charges", "Reconciliation mailboxes", 2.0),
        HandoffRecord("c", "Drishyam movie notes", "Drishyam", 1.0),
    ]
    assert match_handoff("continue on Himanshu initiatives", records).session_id == "a"
    assert match_handoff("update on reconciliation mailboxes", records).session_id == "b"
    # Ambiguous / vague continue with multiple → None (caller should clarify)
    assert match_handoff("continue", records) is None
    assert match_handoff("continue", records[:1]).session_id == "a"


def test_match_requires_clear_winner_for_similar_tasks():
    records = [
        HandoffRecord("a", "Himanshu todos status", "Himanshu todos", 2.0),
        HandoffRecord("b", "Himanshu initiatives report", "Himanshu initiatives", 1.0),
    ]
    # Only "Himanshu" overlaps both → not unique enough
    assert match_handoff("continue Himanshu", records) is None
    assert match_handoff("continue Himanshu initiatives report", records).session_id == "b"
