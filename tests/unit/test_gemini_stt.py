import base64
import httpx
import pytest

from vaani.gemini_stt import GEMINI_TRANSCRIBE_PROMPT, transcribe_with_gemini
from vaani.groq import GroqError


def test_gemini_transcribe_inline(tmp_path):
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        assert "generateContent" in str(req.url)
        body = req.read()
        assert b"inline_data" in body
        assert GEMINI_TRANSCRIBE_PROMPT.encode()[:40] in body
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [{"text": " hello world from gemini "}]
                        }
                    }
                ]
            },
        )

    wav = tmp_path / "x.wav"
    wav.write_bytes(b"RIFF....WAVE")
    result = transcribe_with_gemini(
        wav, "test-key", transport=httpx.MockTransport(handler)
    )
    assert result.text == "hello world from gemini"
    assert result.language == "gemini"
    assert len(seen) == 1


def test_gemini_keeps_raw_text_no_postprocess(tmp_path):
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {"content": {"parts": [{"text": "  raw API text  "}]}}
                ]
            },
        )

    wav = tmp_path / "x.wav"
    wav.write_bytes(b"RIFF")
    result = transcribe_with_gemini(
        wav, "k", transport=httpx.MockTransport(handler)
    )
    assert result.text == "raw API text"


def test_gemini_quota_maps_to_groq_error(tmp_path):
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"message": "quota"}})

    wav = tmp_path / "x.wav"
    wav.write_bytes(b"RIFF")
    with pytest.raises(GroqError) as exc:
        transcribe_with_gemini(wav, "k", transport=httpx.MockTransport(handler))
    assert exc.value.category == "quota"
