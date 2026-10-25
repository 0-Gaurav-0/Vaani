"""Gemini 3.5 Transcribe (free tier) for one meeting part.

Verified 2026-10-23: Files API upload + ``POST /v1beta/interactions`` with
diarization and word timestamps returns ``word_info`` annotations carrying
``speaker`` ("spk:0"), ``start_offset``/``end_offset`` ("1.400s") and
``start_index``/``end_index`` into the block text.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

LOGGER = logging.getLogger("vaani")

BASE = "https://generativelanguage.googleapis.com"
MODEL = "gemini-3.5-transcribe"
REQUEST_TIMEOUT = httpx.Timeout(connect=20.0, read=900.0, write=300.0, pool=20.0)
TRANSIENT_WAITS_S = (20, 60, 120)


class QuotaError(RuntimeError):
    """Free-tier quota hit; retry after ``retry_s`` seconds."""

    def __init__(self, retry_s: float):
        super().__init__(f"quota, retry in {retry_s:.0f}s")
        self.retry_s = retry_s


@dataclass(frozen=True)
class Word:
    text: str
    start: float
    end: float
    speaker: str
    block: int  # index into the response's text blocks
    si: int  # char offsets inside that block's text
    ei: int


def _seconds(value: str | None) -> float:
    try:
        return float(str(value).rstrip("s"))
    except (TypeError, ValueError):
        return 0.0


def parse_words(body: dict) -> tuple[list[str], list[Word]]:
    """Return (block texts, words) from an interactions response."""
    blocks: list[str] = []
    words: list[Word] = []
    for step in body.get("steps") or []:
        for content in step.get("content") or []:
            if content.get("type") != "text":
                continue
            bi = len(blocks)
            text = content.get("text") or ""
            blocks.append(text)
            for ann in content.get("annotations") or []:
                if ann.get("type") != "word_info":
                    continue
                words.append(
                    Word(
                        text=ann.get("text") or "",
                        start=_seconds(ann.get("start_offset")),
                        end=_seconds(ann.get("end_offset")),
                        speaker=str(ann.get("speaker") or "spk:?"),
                        block=bi,
                        si=int(ann.get("start_index") or 0),
                        ei=int(ann.get("end_index") or 0),
                    )
                )
    return blocks, words


def _retry_after(response: httpx.Response) -> float:
    try:
        return max(60.0, float(response.headers.get("retry-after", "")))
    except ValueError:
        return 3600.0


class GeminiTranscriber:
    def __init__(
        self,
        key: str,
        *,
        client: httpx.Client | None = None,
        logger: logging.Logger = LOGGER,
        sleep=time.sleep,
    ):
        self.key = key
        self._sleep = sleep
        self.client = client or httpx.Client(timeout=REQUEST_TIMEOUT)
        self._logger = logger

    def _check(self, response: httpx.Response, what: str) -> None:
        if response.status_code == 429:
            raise QuotaError(_retry_after(response))
        if response.status_code >= 400:
            raise RuntimeError(f"gemini {what} http {response.status_code}: {response.text[:200]}")

    def upload(self, path: Path) -> dict:
        data = path.read_bytes()
        headers = {
            "x-goog-api-key": self.key,
            "X-Goog-Upload-Protocol": "resumable",
            "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(len(data)),
            "X-Goog-Upload-Header-Content-Type": "audio/flac",
        }
        r = self.client.post(f"{BASE}/upload/v1beta/files", headers=headers, json={"file": {"display_name": path.name}})
        self._check(r, "upload-start")
        url = r.headers.get("x-goog-upload-url")
        if not url:
            raise RuntimeError("gemini upload: no upload url")
        r = self.client.post(url, headers={"X-Goog-Upload-Offset": "0", "X-Goog-Upload-Command": "upload, finalize"}, content=data)
        self._check(r, "upload")
        file = r.json()["file"]
        # Large files can be PROCESSING briefly before they are usable.
        deadline = time.monotonic() + 300
        while file.get("state") == "PROCESSING" and time.monotonic() < deadline:
            time.sleep(3)
            g = self.client.get(f"{BASE}/v1beta/{file['name']}", headers={"x-goog-api-key": self.key})
            self._check(g, "file-status")
            file = g.json()
        if file.get("state") not in (None, "ACTIVE"):
            raise RuntimeError(f"gemini file state {file.get('state')}")
        return file

    def transcribe(self, path: Path) -> dict:
        """Upload, transcribe with speakers + word timestamps, delete upload. Returns raw body."""
        file = self.upload(path)
        started = time.monotonic()
        try:
            body = {
                "model": MODEL,
                "input": [{"type": "audio", "uri": file["uri"], "mime_type": "audio/flac"}],
                "generation_config": {
                    "transcription_config": {
                        "language_codes": [],
                        "mode": {"type": "verbatim", "diarization_mode": "speaker"},
                        "timestamp_granularities": ["word"],
                    }
                },
            }
            for wait in (*TRANSIENT_WAITS_S, None):
                r = self.client.post(f"{BASE}/v1beta/interactions", headers={"x-goog-api-key": self.key}, json=body)
                # 500/503 "high demand" is common on the free tier and passes quickly.
                if r.status_code not in (500, 502, 503, 504) or wait is None:
                    break
                self._logger.info("event=meeting_gemini_busy status=%s wait_s=%s", r.status_code, wait)
                self._sleep(wait)
            self._check(r, "transcribe")
            result = r.json()
            if result.get("status") not in (None, "completed"):
                raise RuntimeError(f"gemini status {result.get('status')}")
            self._logger.info(
                "event=meeting_gemini_done file=%s elapsed=%.1f tokens=%s",
                path.name,
                time.monotonic() - started,
                (result.get("usage") or {}).get("total_tokens"),
            )
            return result
        finally:
            try:
                self.client.delete(f"{BASE}/v1beta/{file['name']}", headers={"x-goog-api-key": self.key})
            except Exception:
                pass
