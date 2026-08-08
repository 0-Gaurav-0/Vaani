import json
import threading
from pathlib import Path

from vaani.codex import CodexRunner, HandoffJob


def _fake_hermes(tmp_path: Path, *, sleep_s: float = 0.05, usage_sid: str = "sess_test") -> str:
    early = json.dumps({"session_id": usage_sid, "completed": False})
    late = json.dumps({"session_id": usage_sid, "completed": True})
    script = tmp_path / "hermes"
    script.write_text(
        "#!/bin/sh\n"
        "usage=\n"
        "prev=\n"
        'for a in "$@"; do\n'
        '  if [ "$prev" = "--usage-file" ]; then usage=$a; fi\n'
        "  prev=$a\n"
        "done\n"
        f'if [ -n "$usage" ]; then printf "%s\\n" \'{early}\' > "$usage"; fi\n'
        f"sleep {sleep_s}\n"
        f'if [ -n "$usage" ]; then printf "%s\\n" \'{late}\' > "$usage"; fi\n'
        "echo done-stdout\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return str(script)


def test_start_handoff_accepts_before_complete(tmp_path):
    accepted = threading.Event()
    completed = threading.Event()
    got = {}

    def on_accepted(sid):
        got["accepted_sid"] = sid
        accepted.set()

    def on_complete(result):
        got["result"] = result
        completed.set()

    runner = CodexRunner(
        _fake_hermes(tmp_path, sleep_s=0.35, usage_sid="abc123"),
        cwd=str(tmp_path),
    )
    job = runner.start_handoff(
        "hello agent",
        on_accepted=on_accepted,
        on_complete=on_complete,
        timeout=5.0,
    )
    assert accepted.wait(2.0), "handoff should accept as soon as process starts"
    assert not completed.is_set(), "must not wait for agent finish before accept"
    assert completed.wait(3.0)
    assert got["result"].stdout.strip() == "done-stdout"
    assert got["result"].session_id == "abc123" or job.session_id == "abc123"


def test_handoff_cancel_stops_process(tmp_path):
    accepted = threading.Event()
    completed = threading.Event()
    results = []

    script = tmp_path / "hermes"
    script.write_text("#!/bin/sh\nsleep 30\necho late\n", encoding="utf-8")
    script.chmod(0o755)

    job = HandoffJob(
        prompt="x",
        executable=str(script),
        cwd=str(tmp_path),
        timeout=5.0,
        on_accepted=lambda _sid: accepted.set(),
        on_complete=lambda r: (results.append(r), completed.set()),
    )
    job.start()
    assert accepted.wait(2.0)
    job.cancel()
    assert completed.wait(3.0)
    assert results[0].cancelled or results[0].returncode != 0


def test_command_for_handoff_includes_usage_file(tmp_path):
    job = HandoffJob(
        prompt="hi",
        executable="hermes",
        cwd=str(tmp_path),
        timeout=1.0,
    )
    job._usage_path = tmp_path / "u.json"
    cmd = job._command()
    assert cmd[:3] == ["hermes", "-z", "hi"]
    assert "--skills" in cmd
    assert "vaani" in cmd[cmd.index("--skills") + 1]
    assert "--usage-file" in cmd
    assert str(job._usage_path) in cmd


def test_command_for_skill_handoff_includes_skills_flag(tmp_path):
    job = HandoffJob(
        prompt="do basecamp stuff",
        executable="hermes",
        cwd=str(tmp_path),
        timeout=1.0,
        skill_id="basecamp",
    )
    job._usage_path = tmp_path / "u.json"
    cmd = job._command()
    assert cmd[:3] == ["hermes", "-z", "do basecamp stuff"]
    assert "--skills" in cmd
    skills = cmd[cmd.index("--skills") + 1]
    assert "vaani" in skills
    assert "basecamp" in skills
    assert "--usage-file" in cmd


def test_command_for_resume_handoff(tmp_path):
    job = HandoffJob(
        prompt="also check overdue",
        executable="hermes",
        cwd=str(tmp_path),
        timeout=1.0,
        resume_session="20260806_185320_4ebbce",
        skill_id="basecamp",
    )
    cmd = job._command()
    assert cmd[:3] == ["hermes", "-z", "also check overdue"]
    assert "--resume" in cmd
    assert "20260806_185320_4ebbce" in cmd
    # Vaani skill still preloaded on resume so read-only rules stick.
    assert "--skills" in cmd
    assert "vaani" in cmd[cmd.index("--skills") + 1]
