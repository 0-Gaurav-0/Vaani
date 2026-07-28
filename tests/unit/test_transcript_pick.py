from vaani.groq import pick_latin_transcript


def test_pick_prefers_latin_over_arabic():
    out = pick_latin_transcript("kya haal hai", "کیا حال ہے")
    assert out is not None
    assert "kya" in out.casefold()


def test_pick_prefers_latin_en_when_hi_devanagari():
    out = pick_latin_transcript("kya haal hai", "क्या हाल है")
    assert out == "kya haal hai"


def test_pick_applies_guard():
    assert pick_latin_transcript("Thank you", "Thank you") is None
    assert pick_latin_transcript("Examples", "English") is None


def test_pick_strips_bleed_then_keeps_speech():
    out = pick_latin_transcript(
        "English. open chrome please",
        "open chrome please",
    )
    assert out is not None
    assert "open chrome" in out.casefold()
    assert not out.casefold().startswith("english")
