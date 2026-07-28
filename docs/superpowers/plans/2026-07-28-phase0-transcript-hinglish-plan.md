# Phase 0 — Transcript reliability + Latin Hinglish

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop silence/prompt junk from pasting, and make Hindi/mix speech produce readable Latin Hinglish via double STT + guard.

**Architecture:** Peak-RMS silence reject before Groq; `guard_transcription` rejects/strips bleed; `transcribe` called twice (`en` + `hi`) and a picker chooses the best Latin candidate (transliterate Devanagari; never Arabic/Urdu).

**Tech Stack:** Existing `vaani.groq`, `vaani.audio`, `vaani.controller`, pytest.

---

## File map

| File | Responsibility |
| --- | --- |
| `src/vaani/audio.py` | `wav_peak_rms()` + silence check in validate or helper |
| `src/vaani/groq.py` | Expanded `guard_transcription`; `pick_transcript`; optional `transcribe_latin_hinglish` |
| `src/vaani/controller.py` | Silence ignore; call double-STT helper instead of single `language=en` |
| `tests/unit/test_prompt_guard.py` | Junk reject + edge strip |
| `tests/unit/test_audio.py` or new | Peak RMS / silence |
| `tests/unit/test_groq.py` or new `test_transcript_pick.py` | Picker scoring |

---

### Task 1: Expand bleed / junk guard

**Files:**
- Modify: `src/vaani/groq.py` (`guard_transcription`)
- Test: `tests/unit/test_prompt_guard.py`

- [ ] **Step 1: Write failing tests**

```python
def test_guard_rejects_junk_only():
    assert guard_transcription("Thank you") is None
    assert guard_transcription("Thanks for watching.") is None
    assert guard_transcription("English") is None
    assert guard_transcription("Examples") is None
    assert guard_transcription("English Hinglish Thank you") is None

def test_guard_strips_edge_bleed():
    assert guard_transcription("English. What's the weather in Gujarat") == "What's the weather in Gujarat"
    assert guard_transcription("open chrome please Thank you") == "open chrome please"
    assert guard_transcription("Examples check basecamp") == "check basecamp"
```

- [ ] **Step 2: Run tests — expect fail**
- [ ] **Step 3: Implement strip/reject lists in `guard_transcription`** (casefold; strip repeatedly from ends; reject if remaining empty or only junk tokens)
- [ ] **Step 4: Run tests — expect pass**
- [ ] **Step 5: Commit** `fix(stt): reject and strip Whisper silence/prompt junk`

---

### Task 2: Silence gate via peak RMS

**Files:**
- Modify: `src/vaani/audio.py`
- Modify: `src/vaani/controller.py` (treat silence like too-short)
- Test: `tests/unit/test_audio.py`

- [ ] **Step 1: Failing test** — synthetic near-zero PCM WAV → `wav_peak_rms` below floor; `is_silent_wav` True
- [ ] **Step 2: Implement `wav_peak_rms(path) -> float`** reading PCM after header; floor constant ~0.01 (tune to match waveform silence floor)
- [ ] **Step 3: In `_process`, before `groq.transcribe`, if silent → log `transcription_rejected reason=silence`, emit cancelled/busy like too-short, return
- [ ] **Step 4: Tests pass; commit** `fix(audio): skip STT on near-silent recordings`

---

### Task 3: Double STT picker → Latin Hinglish

**Files:**
- Modify: `src/vaani/groq.py`
- Modify: `src/vaani/controller.py`
- Test: `tests/unit/test_transcript_pick.py` (new)

- [ ] **Step 1: Failing tests for `pick_latin_transcript(en_text, hi_text) -> str | None`**

```python
def test_pick_prefers_latin_over_arabic():
    assert "kya" in pick_latin_transcript("kya haal hai", "کیا حال ہے").casefold()

def test_pick_transliterates_or_prefers_latin_en_when_hi_devanagari():
    # When hi is Devanagari and en is readable latin hinglish, prefer en
    out = pick_latin_transcript("kya haal hai", "क्या हाल है")
    assert out == "kya haal hai"

def test_pick_applies_guard():
    assert pick_latin_transcript("Thank you", "Thank you") is None
```

- [ ] **Step 2: Implement script helpers + picker**
- [ ] **Step 3: Add `GroqClient.transcribe_hinglish(...)`** that runs two `transcribe` calls (`language=en` and `language=hi`), both with Latin-oriented prompt; pick; if winner has Devanagari, call cleanup/light path that transliterates (reuse cleanup instruction already forbidding Devanagari — or strip via a dedicated `to_latin_hinglish` that prefers the other candidate first)
- [ ] **Step 4: Controller uses `transcribe_hinglish` instead of single `language=en`**
- [ ] **Step 5: Unit tests with mocked HTTP or pure picker tests; commit** `fix(stt): double Whisper pick for Latin Hinglish`

---

### Task 4: Verify suite

- [ ] Run `.venv/bin/python -m pytest tests/unit/test_prompt_guard.py tests/unit/test_audio.py tests/unit/test_transcript_pick.py tests/unit/test_groq.py tests/unit/test_controller.py -q`
- [ ] Fix regressions
- [ ] Commit if needed

---

## Manual smoke (after restart when Idle)

1. Hold dictation with mic muted → no paste.
2. Speak English sentence → clean paste.
3. Speak Hindi/mix → Latin Hinglish, not garbage / not Devanagari.
4. Confirm `English. …` style bleed no longer appears at start.
