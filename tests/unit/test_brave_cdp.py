from vaani.platform.linux import brave_cdp


def test_youtube_cdp_action_next_uses_eval(monkeypatch):
    def fake_eval(js, *, await_promise=False):
        return (
            {"ok": True, "via": "a.ytp-next-button"}
            if "ytp-next-button" in js
            else {"ok": False}
        )

    monkeypatch.setattr(brave_cdp, "_eval_youtube", fake_eval)
    assert brave_cdp.youtube_cdp_action("next_track") is True


def test_youtube_cdp_action_false_when_no_tab(monkeypatch):
    monkeypatch.setattr(
        brave_cdp, "_eval_youtube", lambda _js, *, await_promise=False: None
    )
    assert brave_cdp.youtube_cdp_action("next_track") is False


def test_is_youtube_watch():
    assert brave_cdp._is_youtube_watch("https://www.youtube.com/watch?v=abc")
    assert brave_cdp._is_youtube_watch("https://music.youtube.com/watch?v=abc")
    assert not brave_cdp._is_youtube_watch("https://example.com/")
