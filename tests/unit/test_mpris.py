from vaani import mpris


def test_list_and_call_use_busctl(monkeypatch):
    calls = []

    def fake_check_output(cmd, **kwargs):
        assert cmd[:2] == ["busctl", "--user"]
        if cmd[2] == "list":
            return (
                "org.mpris.MediaPlayer2.brave.instance1  1 brave gaurav :1.1 -\n"
                "org.freedesktop.Other  2 x gaurav :1.2 -\n"
            )
        if cmd[2] == "get-property":
            # Allow Next/Previous when CanGo* is true.
            return "b true\n"
        raise AssertionError(cmd)

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return type("R", (), {"returncode": 0})()

    monkeypatch.setattr(mpris.subprocess, "check_output", fake_check_output)
    monkeypatch.setattr(mpris.subprocess, "run", fake_run)

    assert mpris.list_mpris_players() == [
        "org.mpris.MediaPlayer2.brave.instance1"
    ]
    assert mpris.mpris_next()
    assert calls[0][3:7] == [
        "org.mpris.MediaPlayer2.brave.instance1",
        "/org/mpris/MediaPlayer2",
        "org.mpris.MediaPlayer2.Player",
        "Next",
    ]
    assert mpris.mpris_seek_seconds(10)
    assert "Seek" in calls[-1]
    assert "10000000" in calls[-1]
