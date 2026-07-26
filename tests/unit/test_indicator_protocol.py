from pathlib import Path

import pytest

from vaani.indicator_protocol import (
    clear_command,
    control_path,
    read_command,
    write_command,
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
