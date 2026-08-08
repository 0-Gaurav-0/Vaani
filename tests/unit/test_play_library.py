from vaani.play_library import (
    HistoryClip,
    is_playlist_intent,
    is_vague_play,
    match_history_for_query,
    pick_listening_target,
)


def test_vague_and_playlist_intent():
    assert is_vague_play("play a good song")
    assert is_vague_play("play me some other song")
    assert is_vague_play("different playlist")
    assert is_playlist_intent("different playlist")
    assert is_playlist_intent("play another playlist")
    assert not is_vague_play("play sanghu tere")
    assert not is_vague_play("youtube pe kesariya gaana chalao")
    assert not is_vague_play("play anjana")


def test_pick_listening_target_prefers_mix(monkeypatch):
    clips = [
        HistoryClip(
            title="Random news",
            url="https://www.youtube.com/watch?v=aaaaaaaaaaa",
            visit_count=9,
            last_visit=1,
            video_id="aaaaaaaaaaa",
        ),
        HistoryClip(
            title="POV: It's a 9XM Morning || Bollywood songs",
            url="https://www.youtube.com/watch?v=clo67XCJoRE&list=RDxyz",
            visit_count=3,
            last_visit=2,
            video_id="clo67XCJoRE",
        ),
    ]
    monkeypatch.setattr("vaani.play_library._read_browser_youtube_history", lambda limit=80: clips)
    monkeypatch.setattr("vaani.play_library._read_play_memory", lambda limit=40: [])
    monkeypatch.setattr("vaani.play_library.current_youtube_video_id", lambda: "zzzzzzzzzzz")
    picked = pick_listening_target(prefer_playlist=True)
    assert picked is not None
    assert "clo67XCJoRE" in picked.url


def test_match_history_for_query(monkeypatch):
    clips = [
        HistoryClip(
            title="Tu Jaane Na Full Video - Ajab Prem",
            url="https://www.youtube.com/watch?v=BewnzhHlQuk",
            visit_count=2,
            last_visit=3,
            video_id="BewnzhHlQuk",
        )
    ]
    monkeypatch.setattr("vaani.play_library._read_browser_youtube_history", lambda limit=80: clips)
    monkeypatch.setattr("vaani.play_library._read_play_memory", lambda limit=40: [])
    matched = match_history_for_query("tu jaane na")
    assert matched is not None
    assert matched.url.endswith("BewnzhHlQuk")
