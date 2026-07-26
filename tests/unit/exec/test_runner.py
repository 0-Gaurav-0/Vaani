from __future__ import annotations

import ast
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest

from vaani.exec import Command, Completed, powershell, run


def _exe(path: Path, body: str) -> str:
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)
    return str(path)


def test_argv_never_joined(monkeypatch, tmp_path):
    seen: dict[str, object] = {}
    real = subprocess.Popen

    def wrapper(*args, **kwargs):
        seen["args"] = kwargs.get("args", args[0] if args else None)
        seen["shell"] = kwargs.get("shell", False)
        return real(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", wrapper)
    script = _exe(tmp_path / "echoer", 'printf "%s\\n" "$@"')
    result = run(Command(argv=(script, "hello", "world"), timeout=5.0))
    assert isinstance(result, Completed)
    assert seen["shell"] is False
    argv = seen["args"]
    assert isinstance(argv, (list, tuple))
    assert argv == [script, "hello", "world"] or argv == (script, "hello", "world")
    assert all(isinstance(part, str) for part in argv)
    assert not isinstance(argv, str)


def test_no_shell_true_in_src():
    """Reject real ``shell=True`` call kwargs (comments/docstrings allowed)."""
    root = Path(__file__).resolve().parents[3] / "src"
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if kw.arg != "shell":
                    continue
                if isinstance(kw.value, ast.Constant) and kw.value.value is True:
                    offenders.append(f"{path.relative_to(root.parent)}:{node.lineno}")
    assert offenders == []

def test_timeout_kills_process_group(tmp_path):
    pid_file = tmp_path / "child.pid"
    alive = tmp_path / "alive"
    script = _exe(
        tmp_path / "nest",
        f"""
sleep 60 &
echo $! > "{pid_file}"
touch "{alive}"
wait
""",
    )
    started = time.monotonic()
    result = run(Command(argv=(script,), timeout=0.2))
    assert result.timed_out
    assert time.monotonic() - started < 5.0
    # Child should be gone shortly after group kill.
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
            except ValueError:
                break
            try:
                os.kill(pid, 0)
            except OSError:
                break
        else:
            break
        time.sleep(0.05)
    else:
        pytest.fail("process-group child still alive after timeout")


def test_cancel_interrupts(tmp_path):
    script = _exe(tmp_path / "slow", "sleep 30")
    cancel = threading.Event()

    def fire() -> None:
        time.sleep(0.15)
        cancel.set()

    threading.Thread(target=fire, daemon=True).start()
    started = time.monotonic()
    result = run(Command(argv=(script,), timeout=30.0), cancel=cancel)
    assert result.cancelled
    assert not result.timed_out
    assert time.monotonic() - started < 5.0


def test_redaction_on_both_streams(tmp_path):
    script = _exe(
        tmp_path / "leak",
        'echo "api_key=supersecret"\necho "token=also-secret" >&2\n',
    )
    result = run(Command(argv=(script,), timeout=5.0))
    assert "supersecret" not in result.stdout
    assert "also-secret" not in result.stderr
    assert "REDACTED" in result.stdout
    assert "REDACTED" in result.stderr


def test_env_uses_child_environment_plus_extra(monkeypatch, tmp_path):
    monkeypatch.setenv("GROQ_API_KEY", "should-not-leak")
    monkeypatch.setenv("PATH", os.environ.get("PATH", "/usr/bin"))
    out = tmp_path / "env.txt"
    script = _exe(tmp_path / "envdump", f"env > '{out}'\n")
    result = run(
        Command(
            argv=(script,),
            timeout=5.0,
            env_extra={"VAANI_TEST_EXTRA": "1"},
        )
    )
    assert result.returncode == 0
    text = out.read_text()
    assert "GROQ_API_KEY=" not in text
    assert "VAANI_TEST_EXTRA=1" in text


@pytest.mark.parametrize(
    "slot",
    [
        "; Write-Host pwned #",
        "$(Get-Process)",
        "`$(calc)",
        "a'b\"c",
        "& notepad.exe",
    ],
)
def test_powershell_quoting_resists_injection(slot: str):
    argv = powershell(["Write-Output", slot])
    assert argv[:4] == ("powershell", "-NoProfile", "-NonInteractive", "-Command")
    command = argv[4]
    # Slot must appear only as a single-quoted literal (' → '').
    expected = "'" + slot.replace("'", "''") + "'"
    assert expected in command
    # Metacharacters outside quotes would sit between tokens; require quoting.
    assert command.count("'") >= 4
    # The raw unquoted injection fragment must not stand alone as a statement.
    assert f"; {slot}" not in command
