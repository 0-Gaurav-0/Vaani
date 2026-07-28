from vaani.romanize import has_devanagari, romanize_devanagari


def test_romanize_basic_hindi():
    assert has_devanagari("क्या हाल है")
    out = romanize_devanagari("क्या हाल है")
    assert not has_devanagari(out)
    # Approximate Hinglish — accept common variants
    folded = out.casefold().replace(" ", "")
    assert "kya" in folded or "kyaa" in folded
    assert "hai" in folded


def test_romanize_leaves_latin():
    assert romanize_devanagari("chrome kholo") == "chrome kholo"


def test_romanize_mixed():
    out = romanize_devanagari("मुझे chrome खोलो")
    assert "chrome" in out.casefold()
    assert not has_devanagari(out)
