"""Unit tests for Gemini cooldown + dual Whisper en/hi hinglish fallback."""
from __future__ import annotations

from threading import Event
from unittest.mock import patch

import pytest

from vaani.groq import (
    GEMINI_COOLDOWN_S,
    GEMINI_QUOTA_STRIKES,
    GroqClient,
    GroqError,
    TranscriptResult,
)


def _client(*, clock=None) -> GroqClient:
    # No real HTTP — tests mock Gemini + transcribe.
    c = GroqClient(sleep=lambda _: None)
    if clock is not None:
        c._clock = clock
    return c


@pytest.fixture
def gemini_stt_on(monkeypatch):
    monkeypatch.setenv("VAANI_USE_GEMINI", "1")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")


def test_quota_strikes_trip_cooldown_then_skip(tmp_path, caplog, gemini_stt_on):
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"RIFF")
    client = _client()
    langs: list[str | None] = []

    def fake_transcribe(audio, key, *, cancel=None, delete_audio=False, language=None, prompt=None):
        langs.append(language)
        if language == "en":
            return TranscriptResult(
                "Please share the information so I can transcribe this correctly "
                "when I am talking in English.",
                "en",
            )
        return TranscriptResult("inphormeshana", "hi")

    client.transcribe = fake_transcribe  # type: ignore[method-assign]

    with patch(
        "vaani.gemini_stt.transcribe_with_gemini",
        side_effect=GroqError("quota", "quota"),
    ) as gem:
        for _ in range(GEMINI_QUOTA_STRIKES):
            client.transcribe_hinglish(wav, "groq-key")
        assert gem.call_count == GEMINI_QUOTA_STRIKES
        assert client._gemini_in_cooldown()

        langs.clear()
        with caplog.at_level("INFO"):
            out = client.transcribe_hinglish(wav, "groq-key")
        assert gem.call_count == GEMINI_QUOTA_STRIKES
        assert any("event=gemini_cooldown_skip" in r.message for r in caplog.records)
        assert sorted(langs) == ["en", "hi"]
        assert "information" in out.text.casefold()


def test_gemini_success_resets_cooldown(tmp_path, gemini_stt_on):
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"RIFF")
    now = [1000.0]

    def clock() -> float:
        return now[0]

    client = _client(clock=clock)
    client._gemini_quota_strikes = GEMINI_QUOTA_STRIKES
    client._gemini_skip_until = now[0] + GEMINI_COOLDOWN_S
    assert client._gemini_in_cooldown()

    # Expire cooldown window, then succeed once.
    now[0] += GEMINI_COOLDOWN_S + 1.0
    assert not client._gemini_in_cooldown()

    with patch(
        "vaani.gemini_stt.transcribe_with_gemini",
        return_value=TranscriptResult("bhai chrome kholo", "gemini"),
    ):
        out = client.transcribe_hinglish(wav, "groq-key")
    assert out.text == "bhai chrome kholo"
    assert client._gemini_quota_strikes == 0
    assert client._gemini_skip_until == 0.0
    assert not client._gemini_in_cooldown()


def test_non_quota_gemini_fail_does_not_trip_cooldown(tmp_path, gemini_stt_on):
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"RIFF")
    client = _client()
    langs: list[str | None] = []

    def fake_transcribe(audio, key, *, cancel=None, delete_audio=False, language=None, prompt=None):
        langs.append(language)
        return TranscriptResult("hello there friend please", language)

    client.transcribe = fake_transcribe  # type: ignore[method-assign]

    with patch(
        "vaani.gemini_stt.transcribe_with_gemini",
        side_effect=GroqError("network", "down"),
    ):
        for _ in range(5):
            client.transcribe_hinglish(wav, "groq-key")
    assert client._gemini_quota_strikes == 0
    assert not client._gemini_in_cooldown()
    assert langs.count("en") == 5
    assert langs.count("hi") == 5


def test_default_skips_gemini_even_with_key(tmp_path, monkeypatch):
    monkeypatch.delenv("VAANI_USE_GEMINI", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"RIFF")
    client = _client()
    seen: list[str | None] = []

    def fake_transcribe(audio, key, *, cancel=None, delete_audio=False, language=None, prompt=None):
        seen.append(language)
        return TranscriptResult("hello there friend please", language or "en")

    client.transcribe = fake_transcribe  # type: ignore[method-assign]

    with patch("vaani.gemini_stt.transcribe_with_gemini") as gem:
        out = client.transcribe_hinglish(wav, "groq-key")
    gem.assert_not_called()
    assert sorted(seen) == ["en", "hi"]
    assert "hello" in out.text.casefold()


def test_fallback_invokes_en_and_hi(tmp_path):
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"RIFF")
    client = _client()
    seen: list[str | None] = []

    def fake_transcribe(audio, key, *, cancel=None, delete_audio=False, language=None, prompt=None):
        seen.append(language)
        assert language in ("en", "hi")
        assert language is not None  # never auto
        if language == "en":
            return TranscriptResult("Please open Chrome for me I am in a hurry right now", "en")
        return TranscriptResult("bhai chrome kholo jaldi", "hi")

    client.transcribe = fake_transcribe  # type: ignore[method-assign]

    out = client.transcribe_hinglish(wav, "groq-key")
    assert sorted(seen) == ["en", "hi"]
    assert "kholo" in out.text.casefold()


def test_cancel_from_whisper_raises(tmp_path):
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"RIFF")
    client = _client()

    def fake_transcribe(audio, key, *, cancel=None, delete_audio=False, language=None, prompt=None):
        raise GroqError("cancelled", "request cancelled")

    client.transcribe = fake_transcribe  # type: ignore[method-assign]

    with pytest.raises(GroqError) as exc:
        client.transcribe_hinglish(wav, "groq-key")
    assert exc.value.category == "cancelled"


def test_cancel_still_raises_when_gemini_enabled(tmp_path, gemini_stt_on):
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"RIFF")
    client = _client()
    cancel = Event()
    cancel.set()

    with patch(
        "vaani.gemini_stt.transcribe_with_gemini",
        side_effect=GroqError("cancelled", "request cancelled"),
    ):
        with pytest.raises(GroqError) as exc:
            client.transcribe_hinglish(wav, "groq-key", cancel=cancel)
    assert exc.value.category == "cancelled"


def test_delete_audio_in_finally(tmp_path):
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"RIFF")
    client = _client()

    def fake_transcribe(audio, key, *, cancel=None, delete_audio=False, language=None, prompt=None):
        return TranscriptResult("hello from the test please", language)

    client.transcribe = fake_transcribe  # type: ignore[method-assign]

    client.transcribe_hinglish(wav, "groq-key", delete_audio=True)
    assert not wav.exists()


def test_en_fast_does_not_wait_for_slow_hi(tmp_path, monkeypatch, caplog):
    """Clear English must not block on a multi-second Whisper-hi call."""
    import time

    monkeypatch.setattr("vaani.groq.HI_WAIT_FOR_ENGLISH_S", 0.15)
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"RIFF")
    client = _client()

    def fake_transcribe(audio, key, *, cancel=None, delete_audio=False, language=None, prompt=None):
        if language == "en":
            return TranscriptResult(
                "Okay, so the transcription is working but it is way too slow. "
                "I mean, I just transcribed something before.",
                "en",
            )
        time.sleep(2.0)
        return TranscriptResult("चूटकरिस Yeti", "hi")

    client.transcribe = fake_transcribe  # type: ignore[method-assign]
    t0 = time.monotonic()
    with caplog.at_level("INFO"):
        out = client.transcribe_hinglish(wav, "groq-key")
    elapsed = time.monotonic() - t0
    assert elapsed < 1.0
    assert "transcription is working" in out.text.casefold()
    assert any("reason=en_fast" in r.message for r in caplog.records)
