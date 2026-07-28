from vaani.groq import looks_like_english_prose, pick_latin_transcript


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


def test_pick_prefers_hinglish_over_english_translation():
    # Whisper-en often translates; hi/auto may keep spoken words.
    out = pick_latin_transcript(
        "Please open Chrome for me I am in a hurry right now",
        "bhai chrome kholo jaldi",
    )
    assert out is not None
    assert "kholo" in out.casefold()
    assert "please open chrome for me" not in out.casefold()


def test_english_prose_not_confused_with_garbled_romanize():
    english = (
        "What the fuck is this man? I mean, when I am talking in English, "
        "it is saying in Hindi and even that is not correct."
    )
    garbage = (
        "yeh paasa ekishiyalee hiyaa banaa rahee hai ki vaha uthaakshaana "
        "kisa nanbara ke lie preetee hain"
    )
    assert looks_like_english_prose(english)
    assert not looks_like_english_prose(garbage)
