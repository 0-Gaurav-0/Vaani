from vaani.groq import (
    looks_like_english_prose,
    pick_en_hi_transcript,
    pick_latin_transcript,
)


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


def test_pick_en_hi_prefers_en_over_romanize_garbage():
    en = (
        "Please share the information so I can transcribe this correctly "
        "when I am talking in English."
    )
    hi = "inphormeshana traansapraaiba"
    out = pick_en_hi_transcript(en, hi)
    assert out is not None
    assert "information" in out.casefold()
    assert "inphormeshana" not in out.casefold()


def test_pick_en_hi_prefers_en_over_devanagari_english_phonetics():
    en = (
        "What the fuck is this man? I mean, when I am talking in English, "
        "it is saying in Hindi and even that is not correct."
    )
    # Devanagari phonetic of English → romanize garbage; low Hinglish density.
    hi = "इनफॉरमेशन ट्रांस्क्राइब"
    out = pick_en_hi_transcript(en, hi)
    assert out is not None
    assert looks_like_english_prose(out)
    assert "fuck" in out.casefold() or "english" in out.casefold()


def test_pick_en_hi_prefers_hinglish_over_english_translation():
    en = "Please open Chrome for me I am in a hurry right now"
    hi = "bhai chrome kholo jaldi"
    out = pick_en_hi_transcript(en, hi)
    assert out is not None
    assert "kholo" in out.casefold()
    assert "please open chrome for me" not in out.casefold()


def test_pick_en_hi_prefers_en_over_devanagari_hindi_hallucination():
    # Live failure: English speech → Whisper-hi invents Hindi → romanize wins wrongly.
    en = "I'm not going to touch me What is this?"
    hi = "अगर आप यह नहीं लगते हैं, तो आप यह नहीं लगते हैं"
    out = pick_en_hi_transcript(en, hi)
    assert out is not None
    assert "what is this" in out.casefold()
    assert "agara" not in out.casefold()
    assert "lagate" not in out.casefold()


def test_pick_en_hi_rejects_thank_you_plus_short_hi_junk():
    # Live bug: silence/music → en Thank you + hi lyric crumb → YouTube spam.
    assert pick_en_hi_transcript("Thank you.", "प्यार") is None
    assert pick_en_hi_transcript("Thank you.", "झाल") is None
    assert pick_en_hi_transcript("Thank you.", "कर दो") is None
    # Real short command still kept when EN is not junk-only.
    assert pick_en_hi_transcript("pause", "पॉज़") is not None
