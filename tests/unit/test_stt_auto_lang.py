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


def test_hindi_speech_redone_codemixed_then_romanized(tmp_path):
    from vaani.groq import CODEMIX_PROMPT

    seen = []

    def fake(audio, key, *, language=None, prompt=None, **_):
        seen.append((language, prompt))
        if language is None:
            return TranscriptResult("बहुत है जो सेंटर है", "hindi")
        return TranscriptResult("बहुत है जो center है", "hi")

    out = _client(fake).transcribe_hinglish(_wav(tmp_path), "k")
    assert seen == [(None, None), ("hi", CODEMIX_PROMPT)]
    assert out.text == "bahut hai jo center hai"
    assert out.language == "hi"


def test_codemix_prompt_echo_is_discarded(tmp_path):
    from vaani.groq import CODEMIX_PROMPT

    def fake(audio, key, *, language=None, prompt=None, **_):
        if language is None:
            return TranscriptResult("प्ले बीड़ी जलाइले", "hindi")
        return TranscriptResult(CODEMIX_PROMPT, "hi")  # Whisper echoed the prompt

    out = _client(fake).transcribe_hinglish(_wav(tmp_path), "k")
    assert out.text == "play beedi jalaaile"


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


def test_vocab_fixes_split_names(tmp_path, monkeypatch):
    import json
    import vaani.groq as g

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    g._VOCAB_CACHE = None
    assert g.guard_transcription("mujhe sales handy ke mailbox issues") == "mujhe Saleshandy ke mailbox issues"
    (tmp_path / "vaani").mkdir()
    (tmp_path / "vaani" / "vocab.json").write_text(json.dumps({"jev": "Jev"}))
    assert g.guard_transcription("ask jev about it") == "ask Jev about it"
