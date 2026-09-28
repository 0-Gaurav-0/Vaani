from pathlib import Path

import pytest

from vaani.indicator_protocol import (
    ALLOWED_PHASES,
    clear_answer,
    clear_command,
    clear_phase,
    control_path,
    read_answer,
    read_command,
    read_phase,
    write_answer,
    write_command,
    write_phase,
)


def test_write_and_read_command(tmp_path: Path):
    path = tmp_path / "indicator_control.json"
    write_command(path, "stop")
    assert read_command(path) == "stop"
    clear_command(path)
    assert read_command(path) is None


def test_cancel_command(tmp_path: Path):
    path = control_path(tmp_path)
    write_command(path, "cancel")
    assert read_command(path) == "cancel"


def test_reject_unknown_command(tmp_path: Path):
    path = tmp_path / "indicator_control.json"
    with pytest.raises(ValueError):
        write_command(path, "explode")


def test_read_missing_or_corrupt(tmp_path: Path):
    path = tmp_path / "missing.json"
    assert read_command(path) is None
    path.write_text("not-json", encoding="utf-8")
    assert read_command(path) is None


def test_phase_roundtrip(tmp_path: Path):
    path = tmp_path / "indicator_phase"
    write_phase(path, "recording")
    assert read_phase(path) == "recording"
    write_phase(path, "processing")
    assert read_phase(path) == "processing"
    clear_phase(path)
    assert read_phase(path) == "recording"


def test_reject_unknown_phase(tmp_path: Path):
    path = tmp_path / "indicator_phase"
    with pytest.raises(ValueError):
        write_phase(path, "done")


def test_answer_phase_allowed(tmp_path: Path):
    assert "answer" in ALLOWED_PHASES
    path = tmp_path / "indicator_phase"
    write_phase(path, "answer")
    assert read_phase(path) == "answer"


def test_write_and_read_answer(tmp_path: Path):
    path = tmp_path / "indicator_answer.json"
    write_answer(path, "Who is PM?", "Narendra Modi")
    assert read_answer(path) == {
        "question": "Who is PM?",
        "answer": "Narendra Modi",
    }
    clear_answer(path)
    assert read_answer(path) == {"question": "", "answer": ""}
