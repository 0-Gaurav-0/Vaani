from vaani.groq import TRANSCRIPTION_PROMPT, guard_transcription


def test_guard_rejects_prompt_bleed():
    assert guard_transcription(TRANSCRIPTION_PROMPT) is None
    assert guard_transcription("English and Hinglish dictation in Latin letters only.") is None
    assert guard_transcription("hello world") == "hello world"
    cleaned = guard_transcription(
        "English and Hinglish dictation in Latin letters only. open chrome please"
    )
    assert cleaned is None or "open chrome" in cleaned.casefold()
