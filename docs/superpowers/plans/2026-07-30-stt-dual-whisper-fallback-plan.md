# Plan: STT dual-Whisper fallback + Gemini cooldown

Spec: `docs/superpowers/specs/2026-07-30-stt-dual-whisper-fallback-design.md`

Karpathy constraints: surgical edits; wire existing pick helpers; no polish LLM; no play Phase C; no HTTP lock.

## Task 1 — FLAC deps (verify)

Already in tree: `pyproject.toml` / `requirements.*` / `test_audio_upload.py`.

Verify: `pytest tests/unit/test_audio_upload.py` passes; smoke FLAC if needed.

## Task 2 — Pick policy for en+hi pair

In `groq.py`, add `pick_en_hi_transcript(en: str, hi: str) -> str | None`:

- Latinize hi via `_to_latin` when Devanagari.
- If `en` guarded + `looks_like_english_prose(en)` and hinglish density on hi Latin `< 0.12` → return `en`.
- Else return `pick_latin_transcript(en, hi_latin)`.

Tests in `tests/unit/test_transcript_pick.py`:
- `en="...information..."`, `hi` Devanagari/romanize garbage → en
- `en` English translation, `hi` “bhai chrome kholo” → hinglish

## Task 3 — Gemini cooldown state

On `GroqClient`:
- `_gemini_quota_strikes: int`
- `_gemini_skip_until: float` (monotonic)
- Helpers: `_gemini_in_cooldown()`, `_note_gemini_quota_fail()`, `_note_gemini_success()`
- Constants: `GEMINI_QUOTA_STRIKES=3`, `GEMINI_COOLDOWN_S=20*60`

Only `category == "quota"` increments strikes.

## Task 4 — Rewrite `transcribe_hinglish` fallback

1. Skip Gemini if cooldown (log `gemini_cooldown_skip`).
2. Try Gemini; success → note success, return.
3. On quota → note quota fail; other GroqError (non-cancel) → log fallback, no strike.
4. Parallel `transcribe(..., language="en")` and `language="hi"` via `ThreadPoolExecutor(max_workers=2)`.
5. Pick via `pick_en_hi_transcript`; guard; log both previews + pick.
6. Preserve cancel raise; `delete_audio` finally.

Do **not** add `_http_lock`.

## Task 5 — Unit tests for hinglish path

Mock `transcribe` / Gemini:
- Cooldown after 3 quota fails skips Gemini
- Success resets strikes
- Fallback calls en and hi
- Pick uses en for English-vs-romanize case

## Task 6 — Verify

```bash
.venv/bin/pytest tests/unit/test_transcript_pick.py tests/unit/test_audio_upload.py tests/unit/test_gemini_stt.py -q
# plus any new test_transcribe_hinglish / cooldown tests
```

## Out of this plan

Phase C play fail-open; UI fallback toast; Opus; commit message left to user unless asked.
