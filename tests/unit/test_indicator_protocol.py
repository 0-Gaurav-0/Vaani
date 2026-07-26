from pathlib import Path

import pytest

from vaani.indicator_protocol import (
    clear_command,
    clear_options,
    clear_pending_id,
    clear_phase,
    control_path,
    parse_confirm_command,
    parse_select_command,
    read_command,
    read_options,
    read_pending_id,
    read_phase,
    write_command,
    write_options,
    write_pending_id,
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


def test_approve_reject_commands_roundtrip(tmp_path: Path):
    path = tmp_path / "indicator_control.json"
    write_command(path, "approve:abc123")
    assert read_command(path) == "approve:abc123"
    assert parse_confirm_command("approve:abc123") == ("approve", "abc123")
    write_command(path, "reject:abc123")
    assert read_command(path) == "reject:abc123"
    assert parse_confirm_command("reject:abc123") == ("reject", "abc123")
    with pytest.raises(ValueError):
        write_command(path, "approve:")
    with pytest.raises(ValueError):
        write_command(path, "approve:bad id")


def test_select_command_roundtrip(tmp_path: Path):
    path = tmp_path / "indicator_control.json"
    write_command(path, "select:abc123:2")
    assert read_command(path) == "select:abc123:2"
    assert parse_select_command("select:abc123:2") == ("abc123", 1)
    with pytest.raises(ValueError):
        write_command(path, "select:abc123:4")


def test_options_roundtrip(tmp_path: Path):
    path = tmp_path / "indicator_options.json"
    write_options(path, ["a", "b", "c", "d"])
    assert read_options(path) == ["a", "b", "c"]
    clear_options(path)
    assert read_options(path) == []


def test_pending_id_roundtrip(tmp_path: Path):
    path = tmp_path / "indicator_pending"
    write_pending_id(path, "deadbeef")
    assert read_pending_id(path) == "deadbeef"
    clear_pending_id(path)
    assert read_pending_id(path) is None


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
