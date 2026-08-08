from types import SimpleNamespace

from vaani.media import resolve_media_action, run_media_action


def test_resolve_pause_next_prev_stop():
    assert resolve_media_action("pause").method == "pause"
    assert resolve_media_action("pause the song").method == "pause"
    assert resolve_media_action("next song").method == "next_track"
    assert resolve_media_action("play the next track").method == "next_track"
    assert resolve_media_action("skip").method == "next_track"
    assert resolve_media_action("previous song").method == "previous_track"
    assert resolve_media_action("go back to the previous track").method == "previous_track"
    assert resolve_media_action("stop the music").method == "stop"
    assert resolve_media_action("agla gana").method == "next_track"
    assert resolve_media_action("pichla gana").method == "previous_track"


def test_resolve_seek_and_times():
    fwd = resolve_media_action("skip ahead a few seconds")
    assert fwd is not None and fwd.method == "seek_forward" and fwd.times == 1
    fwd30 = resolve_media_action("skip forward 30 seconds")
    assert fwd30 is not None and fwd30.method == "seek_forward" and fwd30.times == 3
    back = resolve_media_action("go back 10 seconds")
    assert back is not None and back.method == "seek_backward" and back.times == 1


def test_resolve_resume_and_toggle():
    assert resolve_media_action("resume").method == "play"
    assert resolve_media_action("play").method == "play"
    assert resolve_media_action("Play.").method == "play"
    # Live STT: "play" → "Bless you."
    assert resolve_media_action("Bless you.").method == "play"
    assert resolve_media_action("stop").method == "stop"
    assert resolve_media_action("continue playing").method == "play"
    assert resolve_media_action("play pause").method == "play_pause"
    assert resolve_media_action("go to the next song").method == "next_track"


def test_resolve_does_not_steal_play_song():
    assert resolve_media_action("play coldplay song on youtube") is None
    assert resolve_media_action("play despacito song") is None
    assert resolve_media_action("open chrome") is None


def test_run_media_calls_sender(monkeypatch):
    # Force XF86 fallback so unit tests don't hit live YouTube/MPRIS/CDP.
    monkeypatch.setattr("vaani.media._run_via_youtube_cdp", lambda _a: False)
    monkeypatch.setattr("vaani.media._run_via_youtube_keys", lambda _a: False)
    monkeypatch.setattr("vaani.media._run_via_mpris", lambda _a: False)

    calls = []

    class Fake:
        def pause(self):
            calls.append("pause")

        def play_pause(self):
            calls.append("play_pause")

        def next_track(self):
            calls.append("next")

        def seek_forward(self):
            calls.append("seek_fwd")

        def _tap_xf86(self, attr, *, times=1):
            calls.append((attr, times))

    msg = run_media_action(resolve_media_action("pause"), sender=Fake())
    assert msg == "Paused."
    assert calls == ["pause"]

    calls.clear()
    msg = run_media_action(resolve_media_action("next"), sender=Fake())
    assert msg == "Next track."
    assert calls == ["next"]

    calls.clear()
    msg = run_media_action(
        resolve_media_action("skip forward 20 seconds"), sender=Fake()
    )
    assert "Seek forward" in msg
    assert calls == [("XK_XF86_AudioForward", 2)]

    calls.clear()
    msg = run_media_action(resolve_media_action("play"), sender=Fake())
    assert msg == "Playing."
    assert calls == ["play_pause"]


def test_run_media_prefers_cdp_even_when_sender_set(monkeypatch):
    """Regression: next/skip must use Brave CDP (works minimized), not XF86."""
    seen = []

    monkeypatch.setattr(
        "vaani.media._run_via_youtube_cdp",
        lambda action: seen.append(("cdp", action.method)) or True,
    )
    monkeypatch.setattr(
        "vaani.media._run_via_youtube_keys",
        lambda action: seen.append(("keys", action.method)) or True,
    )
    monkeypatch.setattr(
        "vaani.media._run_via_mpris",
        lambda action: seen.append(("mpris", action.method)) or True,
    )

    class Fake:
        def next_track(self):
            seen.append("xf86")

    msg = run_media_action(resolve_media_action("skip"), sender=Fake())
    assert msg == "Next track."
    assert seen == [("cdp", "next_track")]
