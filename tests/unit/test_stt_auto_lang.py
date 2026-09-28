"""Auto language detect (dictation) + en/hi candidates (assistant)."""
from vaani.groq import GroqClient, TranscriptResult


def _client(fake):
    c = GroqClient(sleep=lambda _: None)
    c.transcribe = fake  # type: ignore[method-assign]
    return c


def _wav(tmp_path):
    p = tmp_path / "a.wav"
    p.write_bytes(b"RIFF")
    return p


def test_english_speech_is_one_auto_call(tmp_path):
    seen = []

    def fake(audio, key, *, language=None, **_):
        seen.append(language)
        return TranscriptResult("Can you check the reconciliation dashboard today?", "english")

    out = _client(fake).transcribe_hinglish(_wav(tmp_path), "k")
    assert seen == [None]
    assert out.text == "Can you check the reconciliation dashboard today?"
    assert out.language == "en"


def test_hindi_speech_is_romanized_latin_hinglish(tmp_path):
    seen = []

    def fake(audio, key, *, language=None, **_):
        seen.append(language)
        return TranscriptResult("प्ले बीड़ी जलाइले", "hindi")

    out = _client(fake).transcribe_hinglish(_wav(tmp_path), "k")
    assert seen == [None]
    assert out.text == "play beedi jalaaile"
    assert out.language == "hi"


def test_urdu_misdetect_retries_forced_hindi(tmp_path):
    seen = []

    def fake(audio, key, *, language=None, **_):
        seen.append(language)
        if language is None:
            return TranscriptResult("مجھے کل کی میٹنگ کے بارے میں بتاؤ", "urdu")
        return TranscriptResult("मुझे कल की मीटिंग के बारे में बताओ", "hi")

    out = _client(fake).transcribe_hinglish(_wav(tmp_path), "k")
    assert seen == [None, "hi"]
    assert out.text == "mujhe kal ki meeting ke baare mein batao"


def test_assistant_parallel_keeps_both_candidates(tmp_path):
    def fake(audio, key, *, language=None, **_):
        if language == "en":
            return TranscriptResult("Play B.V", "en")
        return TranscriptResult("प्ले बीड़ी जलाइले", "hi")

    out = _client(fake).transcribe_hinglish(_wav(tmp_path), "k", parallel=True)
    assert out.candidates == ("Play B.V", "play beedi jalaaile")
