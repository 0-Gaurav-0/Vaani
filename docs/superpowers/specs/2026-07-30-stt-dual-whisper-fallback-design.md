# Design: STT dual-Whisper fallback + FLAC + Gemini cooldown

Date: 2026-07-30  
Branch baseline: `save/gemini-hinglish-accurate-2026-07-30`  
Work branch: `feat/assistant-qa-pill-critical`

## Problem

Gemini primary STT (Latin Hinglish + English mix) is accurate when healthy, but currently fails with HTTP 402/429 (`gemini_transcribe_fallback detail=quota`) on every request. The silent Groq fallback uses a single Whisper `language=auto` call; when LID chooses Hindi, English is emitted as Devanagari phonetics and `romanize_devanagari` turns it into garbage (`information` → `inphormeshana`, `transcribe` → `traansapraaiba`).

Linux also uploaded raw 16 kHz WAV (no `soundfile`), inflating upload latency. Assistant “play” fails when STT drops the verb and clarify times out with no action — **out of scope for this PR** (Phase C).

## Goals

1. Fast + accurate **English spelling** and **Hindi as chat-style Latin Hinglish**; mixed speech kept as spoken; **no translate**.
2. When Gemini is down, Groq fallback must still be correct — not “best-effort romanize.”
3. Avoid a failed Gemini round-trip on every dictation during quota outages.
4. Lossless FLAC after stop before upload (already approved).

## Non-goals (v1)

- Live encode during recording; Opus/MP3 lossy path
- Local Whisper; EN/HI user mode toggle
- Re-enabling broad LLM cleanup / Hinglish polish
- Assistant play fail-open (Phase C, separate PR)
- Per-clause language picking inside one utterance (accept Gemini handles mixed when up)

## Architecture

### Phase A — FLAC upload (shipped in tree)

`prepare_transcription_upload` already prefers FLAC via `soundfile`. Linux deps now include `numpy` + `soundfile`. Verify `event=audio_compress` in live logs after Vaani restart.

### Phase B — Provider roles (this design)

| Provider | Role |
|----------|------|
| Gemini (when not cooling down) | Primary: steerable Latin Hinglish + English orthography |
| Groq Whisper `en` | English spelling specialist candidate |
| Groq Whisper `hi` | Hindi/Hinglish candidate (may be Devanagari → Latin only as last resort) |
| Mechanical `romanize_devanagari` | Script strip for hi candidate only — **never** sole product path |

**Flow**

1. If `GEMINI_API_KEY` set and cooldown not active → try Gemini; on success paste raw (`language=gemini`).
2. On Gemini `quota` (402/429): increment consecutive quota strikes. After **N=3**, set cooldown **20 minutes** (quota-only; network/timeout do not trip cooldown).
3. On any Gemini hard fail (or cooldown skip): run **parallel** Whisper `language=en` and `language=hi` (not auto).
4. Pick winner with explicit English-vs-Hinglish policy (wire existing helpers; extend slightly):
   - If `en` looks like English prose **and** `hi` Latin lacks strong Hinglish density → take `en` (stops `inphormeshana`).
   - If `hi` has strong Hinglish density → prefer Hinglish via `pick_latin_transcript` (stops Whisper-en translating Hindi).
   - Else `pick_latin_transcript(en, hi_latin)`.
5. On Gemini success: clear strikes + cooldown.
6. Log: provider, cooldown skip, both candidate previews, pick reason. No UI change required in v1.

**Concurrency:** parallel Whisper must not reintroduce a process-wide HTTP lock (that previously serialized dictation to 10–17s). Prefer thread-safe concurrent requests or short-lived clients per call.

**Cancel:** existing cancel/token discard remains; do not block Phase B on perfect Gemini HTTP abort.

### Phase C — Play media fail-open (separate PR)

When clarify options are all `intent=play`, timeout executes YouTube option instead of clearing. Soft media signals may bias router. Not implemented here.

## Assumptions (explicit)

- User priority is EN+HI accuracy/speed; other languages later.
- Doubling Groq Whisper calls during Gemini outage is acceptable vs wrong text.
- No Hinglish LLM polish in v1 (Karpathy: simplest correct path).
- Council (Claude 2026-07-30): endorse Phase B; `en+hi` not `en+auto`; cooldown N=3 / 20m; Phase C separate. Codex run incomplete — Claude opinion used.

## Success criteria

1. With Gemini disabled/quota: 10 English utterances paste correct English spelling (no phonetic Devanagari-romanize).
2. With Gemini disabled/quota: 10 Hindi/Hinglish utterances stay Latin Hinglish, not English translations of the whole line.
3. During sustained Gemini 429: after 3 strikes, subsequent dictations skip Gemini (log `gemini_cooldown_skip`) until window ends.
4. Unit tests: cooldown trip/reset; pick prefers `en` over romanized English-Devanagari; pick prefers Hinglish over English translation; parallel path invokes both languages.
5. FLAC: `event=audio_compress` appears for normal clips after restart.

## Risks

- 2× Groq STT volume under Gemini outage → possible Groq rate limits; monitor logs.
- Mixed EN+HI in one sentence may still be imperfect on Groq-only; Gemini remains best for that when healthy.
- `httpx.Client` concurrent use — verify with tests; fall back to two clients if needed.
