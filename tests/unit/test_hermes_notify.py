from vaani import hermes_notify as hn


def test_preview_truncates():
    assert hn._preview("hello") == "hello"
    long = "x" * 200
    out = hn._preview(long, limit=20)
    assert len(out) == 20
    assert out.endswith("…")


def test_open_hermes_session_uses_session_deep_link(monkeypatch):
    calls = []
    monkeypatch.setattr(
        hn.subprocess,
        "Popen",
        lambda *a, **k: calls.append(a[0] if a else []) or type("P", (), {"pid": 1})(),
    )
    monkeypatch.setattr(
        hn.shutil, "which", lambda name: "/bin/hermes-desktop" if name == "hermes-desktop" else None
    )
    monkeypatch.setattr(hn, "latest_vani_task_session", lambda **k: None)
    hn.open_hermes_session("sess123")
    assert calls
    joined = " ".join(str(x) for x in calls[0])
    assert "hermes://session/sess123" in joined


def test_notify_handoff_spawns_helper(monkeypatch, tmp_path):
    calls = []
    helper = tmp_path / "vaani-notify-helper.py"
    helper.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    monkeypatch.setattr(hn, "_helper_cmd", lambda: ["/usr/bin/python3", str(helper)])
    monkeypatch.setattr(
        hn.subprocess,
        "Popen",
        lambda *a, **k: calls.append(a[0] if a else []) or type("P", (), {"pid": 1})(),
    )
    monkeypatch.setattr(hn, "latest_vani_task_session", lambda **k: "sid9")
    hn.notify_handoff(kind="done", task="say hello", session_ref={"id": "sid9"})
    assert calls
    assert "say hello" in " ".join(str(x) for x in calls[0])
    assert "sid9" in calls[0]
