from datetime import datetime
from pathlib import Path

from vaani.memory import append_turn, day_path, load_context


def test_append_and_load_day_memory(tmp_path: Path):
    when = datetime(2026, 7, 27, 22, 18)
    append_turn(
        "How do I make a cup of tea?",
        "Boil water and steep tea.",
        when=when,
        root=tmp_path,
    )
    append_turn(
        "Give me the detailed instruction.",
        "1. Boil water. 2. Steep.",
        when=datetime(2026, 7, 27, 22, 19),
        root=tmp_path,
    )
    path = day_path(when, root=tmp_path)
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "How do I make a cup of tea?" in text
    assert "detailed instruction" in text

    ctx = load_context(when=when, root=tmp_path)
    assert "How do I make a cup of tea?" in ctx
    assert "Give me the detailed instruction." in ctx


def test_load_context_missing_day_is_empty(tmp_path: Path):
    assert load_context(when=datetime(2026, 1, 1), root=tmp_path) == ""
