"""Gemini multimodal speech-to-text (optional; requires GEMINI_API_KEY)."""
from __future__ import annotations

import base64
import logging
import os
import time
from pathlib import Path
from threading import Event
from typing import Any, Callable

import httpx

from .audio_upload import prepare_transcription_upload
from .groq import GroqError, TranscriptResult

LOGGER = logging.getLogger("vaani")

# Steerable transcript rules — Gemini follows these better than Whisper prompts.
GEMINI_TRANSCRIBE_PROMPT = (
    "Transcribe this speech exactly as spoken. "
    "English words → English spelling. "
    "Hindi words → Latin-script Hinglish (not Devanagari, not Arabic/Urdu). "
    "If English and Hindi are mixed in one sentence, keep that mix. "
    "Do not translate. Do not summarize. Do not add punctuation commentary. "
    "Return only the transcript text."
)

DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"


def gemini_api_key(environ: dict[str, str] | None = None) -> str | None:
    env = os.environ if environ is None else environ
    value = (env.get("GEMINI_API_KEY") or "").strip()
    return value or None


def gemini_model(environ: dict[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    return (env.get("VAANI_GEMINI_MODEL") or DEFAULT_GEMINI_MODEL).strip()


def _mime_for(path: Path, content_type: str) -> str:
    if content_type.startswith("audio/"):
        return content_type
    suffix = path.suffix.casefold()
    if suffix == ".flac":
        return "audio/flac"
    if suffix in {".mp3", ".mpeg"}:
        return "audio/mp3"
    if suffix in {".m4a", ".mp4"}:
        return "audio/mp4"
    return "audio/wav"


def transcribe_with_gemini(
    audio: Path,
    key: str,
    *,
    cancel: Event | None = None,
    model: str | None = None,
    clock: Callable[[], float] = time.monotonic,
    logger: logging.Logger = LOGGER,
    transport: httpx.BaseTransport | None = None,
) -> TranscriptResult:
    """Upload audio to Gemini generateContent; return Latin transcript."""
    if cancel and cancel.is_set():
        raise GroqError("cancelled", "request cancelled")
    upload_path = Path(audio)
    upload_name = upload_path.name
    content_type = "audio/wav"
    temp_upload = False
    started = clock()
    try:
        upload_path, upload_name, content_type, temp_upload = prepare_transcription_upload(
            Path(audio)
        )
        raw_bytes = upload_path.read_bytes()
        mime = _mime_for(upload_path, content_type)
        b64 = base64.standard_b64encode(raw_bytes).decode("ascii")
        model_id = model or gemini_model()
        url = f"{GEMINI_BASE}/models/{model_id}:generateContent"
        payload: dict[str, Any] = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": GEMINI_TRANSCRIBE_PROMPT},
                        {"inline_data": {"mime_type": mime, "data": b64}},
                    ],
                }
            ],
            "generationConfig": {
                "temperature": 0,
                "maxOutputTokens": 2048,
            },
        }
        logger.info(
            "event=gemini_transcribe_start bytes=%s model=%s mime=%s",
            len(raw_bytes),
            model_id,
            mime,
        )
        timeout = httpx.Timeout(90.0, connect=15.0)
        with httpx.Client(transport=transport, timeout=timeout) as client:
            response = client.post(url, params={"key": key}, json=payload)
        if cancel and cancel.is_set():
            raise GroqError("cancelled", "request cancelled")
        if response.status_code in (401, 403):
            raise GroqError("http", f"gemini auth {response.status_code}")
        if response.status_code in (402, 429):
            raise GroqError("quota", f"gemini quota {response.status_code}")
        if response.status_code >= 400:
            raise GroqError("http", f"gemini http {response.status_code}")
        try:
            body = response.json()
            parts = body["candidates"][0]["content"]["parts"]
            chunks = [
                p.get("text", "")
                for p in parts
                if isinstance(p, dict) and isinstance(p.get("text"), str)
            ]
            text = " ".join(chunks).strip()
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise GroqError("malformed", "invalid gemini transcription") from exc
        if not text:
            raise GroqError("malformed", "empty gemini transcription")
        # Iteration: paste Gemini output as-is (no guard / romanize / cleanup).
        logger.info(
            "event=gemini_transcribe_done chars=%s elapsed=%.2f preview=%r",
            len(text),
            clock() - started,
            (text[:80] + "…") if len(text) > 80 else text,
        )
        return TranscriptResult(text, "gemini")
    finally:
        if temp_upload:
            try:
                upload_path.unlink(missing_ok=True)
            except OSError:
                pass
