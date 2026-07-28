from types import SimpleNamespace

from vaani.volume import resolve_volume_action, run_volume_action


def test_resolve_mute_unmute_and_steps():
    assert resolve_volume_action("mute").name == "mute"
    assert resolve_volume_action("unmute").name == "unmute"
    assert resolve_volume_action("volume up").argv[-1] == "+5%"
    assert resolve_volume_action("volume down").argv[-1] == "-5%"
    assert resolve_volume_action("set volume to 30").argv[-1] == "30%"
    assert resolve_volume_action("awaz band karo").name == "mute"


def test_resolve_ignores_unrelated():
    assert resolve_volume_action("open chrome") is None
    assert resolve_volume_action("what is muteable design") is None


def test_run_volume_uses_pactl_argv(monkeypatch):
    seen = []

    def runner(argv):
        seen.append(list(argv))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("vaani.volume.shutil.which", lambda _n: "/usr/bin/pactl")
    msg = run_volume_action(resolve_volume_action("mute"), runner=runner)
    assert msg == "Muted."
    assert seen == [["/usr/bin/pactl", "set-sink-mute", "@DEFAULT_SINK@", "1"]]
