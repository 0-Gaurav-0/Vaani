from vaani.wake_phrase import (
    extract_wake_assistant,
    is_wake_hallucination,
    wake_name_matches,
)


def test_wake_name_aliases_and_near_miss():
    assert wake_name_matches("Vaani")
    assert wake_name_matches("vani")
    assert wake_name_matches("Wani")
    assert wake_name_matches("Vernie")
    assert wake_name_matches("vaanii")  # levenshtein to vaani
    assert wake_name_matches("wanii")
    assert wake_name_matches("bunny")
    assert wake_name_matches("Rami")
    assert wake_name_matches("Bani")
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
    assert extract_wake_assistant("hey bunny") == ""
    assert extract_wake_assistant("okay Rami") == ""
    assert extract_wake_assistant("Hey, honey") == ""
    assert extract_wake_assistant("Hey, Vahey") == ""
    assert extract_wake_assistant("Hey, onee") == ""
    assert extract_wake_assistant("OKAY VANI") == ""
    assert extract_wake_assistant("play a song") is None
    assert extract_wake_assistant("hey youtube play music") is None
    # Loose single-letter prefixes removed (too many false wakes).
    assert extract_wake_assistant("A Vani") is None
    assert extract_wake_assistant("a bani open youtube") is None
    # Bare name alone wakes (clear brand forms only).
    assert extract_wake_assistant("Vaani") == ""
    assert extract_wake_assistant("Vaani.") == ""
    assert extract_wake_assistant("Vaani!") == ""
    assert extract_wake_assistant("vani") == ""
    assert extract_wake_assistant("Vaani play a song") == "play a song"
    assert extract_wake_assistant("Hivani") == ""
    assert extract_wake_assistant("HeyVaani") == ""
    assert extract_wake_assistant("hey warning") == ""
    # Loose STT mangling still needs hey/okay when used alone.
    assert extract_wake_assistant("bunny") is None
    assert extract_wake_assistant("Barney") is None


def test_wake_hallucinations():
    assert is_wake_hallucination("Thank you.")
    assert is_wake_hallucination("you")
    assert is_wake_hallucination("Okay.")
    assert is_wake_hallucination("Bye.")
    assert not is_wake_hallucination("Hey bunny!")
    assert not is_wake_hallucination("okay Vaani")
    assert not is_wake_hallucination("Vaani")

