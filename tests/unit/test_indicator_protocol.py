from pathlib import Path

import pytest

from vaani.indicator_protocol import (
    clear_command,
    clear_phase,
    control_path,
    read_command,
    read_phase,
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
    for phase in ("recording", "processing", "confirming", "working"):
        write_phase(path, phase)
        assert read_phase(path) == phase
    clear_phase(path)
    assert read_phase(path) == "recording"


def test_reject_unknown_phase(tmp_path: Path):
    path = tmp_path / "indicator_phase"
    with pytest.raises(ValueError):
        write_phase(path, "done")


def test_unknown_phase_falls_back_to_recording(tmp_path: Path):
    path = tmp_path / "indicator_phase"
    path.write_text("done", encoding="utf-8")
    assert read_phase(path) == "recording"
    path.write_text("not-a-phase", encoding="utf-8")
    assert read_phase(path) == "recording"
