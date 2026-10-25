from vaani.assistant_route import (
    RouteDecision,
    RouteOption,
    format_clarify_body,
    match_clarify_choice,
    parse_route_payload,
)


def test_parse_route_payload_play():
    decision = parse_route_payload(
        '{"intent":"play","query":"spiderman brand new day trailer",'
        '"target":"youtube","confidence":0.91,"options":[]}'
    )
    assert decision is not None
    assert decision.intent == "play"
    assert decision.target == "youtube"
    assert decision.confidence == 0.91
    assert not decision.should_clarify


def test_parse_route_payload_clarify_options():
    decision = parse_route_payload(
        """```json
        {"intent":"clarify","query":"brand new day","target":"",
         "confidence":0.4,"options":[
           {"label":"Play on YouTube","intent":"play","query":"brand new day trailer","target":"youtube"},
           {"label":"Search Netflix","intent":"play","query":"brand new day","target":"netflix"}
         ]}
        ```"""
    )
    assert decision is not None
    assert decision.should_clarify
    assert len(decision.options) == 2
    assert decision.options[0].target == "youtube"


def test_match_clarify_choice_number_and_label():
    options = (
        RouteOption("Play on YouTube", "play", "x", "youtube"),
        RouteOption("Search Netflix", "play", "x", "netflix"),
    )
    assert match_clarify_choice("2", options).target == "netflix"
    assert match_clarify_choice("option 1", options).target == "youtube"
    assert match_clarify_choice("youtube please", options).target == "youtube"
    assert match_clarify_choice("something else entirely", options) is None


def test_format_clarify_body():
    body = format_clarify_body(
        [RouteOption("Play on YouTube", "play", "a", "youtube")]
    )
    assert body.startswith("1. Play on YouTube")


def test_resolve_play_target_youtube(monkeypatch):
    from vaani.sites import resolve_play_target

    monkeypatch.setattr(
        "vaani.sites._first_youtube_watch_url",
        lambda query, timeout=2.5: "https://www.youtube.com/watch?v=abcdefghijk",
    )
    target = resolve_play_target(
        "spiderman brand new day trailer", "youtube"
    )
    assert target is not None
    assert target.url.endswith("watch?v=abcdefghijk")


def test_disagree_play_choices_offers_both_titles_when_jev_is_down():
    from vaani.controller import disagree_play_choices

    # Live failure: forced-English Whisper translated the Hindi title.
    decision = disagree_play_choices(("Play in the first sight", "Play pahali najar mein jaaega"))
    assert decision is not None and decision.should_clarify
    assert [o.query for o in decision.options] == ["in the first sight", "pahali najar mein jaaega"]
    assert all(o.intent == "play" and o.target == "youtube" for o in decision.options)


def test_disagree_play_choices_skips_non_play_or_identical():
    from vaani.controller import disagree_play_choices

    assert disagree_play_choices(("Flip and", "play pe")) is None
    assert disagree_play_choices(("play tum hi ho", "Play Tum Hi Ho")) is None
