# Hindi/Hinglish Accuracy Implementation Plan

> **For agentic workers:** Implement inline in the current checkout; use TDD for each behavior.

**Goal:** Improve Hindi and mixed Hindi-English dictation by producing readable Latin-script Hinglish while preserving the existing fast English path.

**Architecture:** Keep Whisper language detection automatic. Add a transcription prompt that biases output toward Latin-script Hinglish. Pass the detected transcript shape into cleanup so Devanagari/Hinglish gets a transliteration-specific instruction and validation, while English-only cleanup keeps the existing anti-rewrite guard.

**Tech Stack:** Python, httpx, Groq OpenAI-compatible transcription/chat APIs, pytest.

## Global Constraints

- Keep `whisper-large-v3-turbo` for transcription.
- Keep `llama-3.1-8b-instant` for cleanup.
- Do not force all recordings to Hindi; English must remain automatic and fast.
- Hindi output must prefer Latin-script Hinglish, not Devanagari.
- Do not invent content or rewrite English speech.
- Preserve cancellation and existing request deadlines.

---

### Task 1: Hindi-aware transcription request

**Files:**
- Modify: `src/vaani/groq.py`
- Test: `tests/unit/test_groq.py`

- [x] Add a failing request-inspection test asserting the transcription multipart body includes a Latin-script Hinglish prompt.
- [x] Run that focused test and confirm it fails because `prompt` is absent.
- [x] Add a constant prompt and include it in the transcription form data without forcing a language code.
- [x] Run the focused test and confirm it passes.

### Task 2: Hindi-aware cleanup and validation

**Files:**
- Modify: `src/vaani/groq.py`
- Test: `tests/unit/test_groq.py`

- [ ] Add failing tests for Devanagari input being sent with transliteration guidance and for valid transliteration not being rejected as divergent.
- [ ] Run the focused tests and confirm they fail.
- [ ] Add a helper that identifies Hindi/Devanagari or mixed Hinglish input and selects a transliteration-specific cleanup instruction.
- [ ] Extend divergence validation so script conversion is allowed for Hindi-aware cleanup, while English-only cleanup remains strict.
- [ ] Run focused tests and confirm they pass.

### Task 3: Full regression verification and runtime restart

**Files:**
- No additional production files.

- [ ] Run all unit tests.
- [ ] Run Python compilation.
- [ ] Restart the Windows Vaani process with the currently configured process-local Groq key.
- [ ] Verify the daemon is running and hotkeys are registered.
