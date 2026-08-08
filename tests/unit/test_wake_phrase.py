from vaani.wake_phrase import extract_wake_assistant, wake_name_matches


def test_wake_name_aliases_and_near_miss():
    assert wake_name_matches("Vaani")
    assert wake_name_matches("vani")
    assert wake_name_matches("Wani")
    assert wake_name_matches("Vernie")
    assert wake_name_matches("vaanii")  # levenshtein to vaani
    assert wake_name_matches("wanii")
    assert not wake_name_matches("youtube")
    assert not wake_name_matches("hey")


def test_extract_wake_variants():
    assert extract_wake_assistant("hey Vaani") == ""
    assert extract_wake_assistant("Hey, Vani!") == ""
    assert extract_wake_assistant("Hey Vani play a song") == "play a song"
    assert extract_wake_assistant("Hey, Vaani play despacito") == "play despacito"
    assert extract_wake_assistant("hey Wani, open YouTube") == "open YouTube"
    assert extract_wake_assistant("hi vernie what time is it") == "what time is it"
    assert extract_wake_assistant("okay vaanee check my todos") == "check my todos"
    assert extract_wake_assistant("oye vani status of this todo") == (
        "status of this todo"
    )
    assert extract_wake_assistant("hey Barney") == ""
    assert extract_wake_assistant("play a song") is None
    assert extract_wake_assistant("hey youtube play music") is None
