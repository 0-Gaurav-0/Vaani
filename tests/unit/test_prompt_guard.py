from vaani.groq import TRANSCRIPTION_PROMPT, guard_transcription


def test_guard_rejects_prompt_bleed():
    assert guard_transcription(TRANSCRIPTION_PROMPT) is None
    assert guard_transcription("English and Hinglish dictation in Latin letters only.") is None
    assert guard_transcription("hello world") == "hello world"
    assert guard_transcription("Write Hindi words in Latin") is None
    cleaned = guard_transcription(
        "English and Hinglish dictation in Latin letters only. open chrome please"
    )
    assert cleaned is None or "open chrome" in cleaned.casefold()


def test_guard_rejects_junk_only():
    assert guard_transcription("Thank you") is None
    assert guard_transcription("Thanks for watching.") is None
    assert guard_transcription("English") is None
    assert guard_transcription("Examples") is None
    assert guard_transcription("English Hinglish Thank you") is None
    assert guard_transcription("So much for you") is None


def test_guard_strips_edge_bleed():
    assert (
        guard_transcription("English. What's the weather in Gujarat")
        == "What's the weather in Gujarat"
    )
    assert guard_transcription("open chrome please Thank you") == "open chrome please"
    assert guard_transcription("Examples check basecamp") == "check basecamp"


def test_guard_does_not_censor_slang():
    assert guard_transcription("gaandu") == "gaandu"
    assert guard_transcription("tu gaandu hai yaar") == "tu gaandu hai yaar"
    assert guard_transcription("fuck this shit bhai") == "fuck this shit bhai"


def test_guard_strips_eqamples_misspelling_mid_sentence():
    raw = (
        "Eqamples and Hinglish, Chuhnaan speaking, it seems like it was a few "
        "seconds slow."
    )
    cleaned = guard_transcription(raw)
    assert cleaned is not None
    assert "eqamples" not in cleaned.casefold()
    assert "hinglish" not in cleaned.casefold()
    assert "seconds slow" in cleaned.casefold()
