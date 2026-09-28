# Assistant Groq Q&A + critical fixes

**Date:** 2026-07-27  
**Status:** Approved for implementation (Approach 1)  
**Base:** `save/stable` (`1009ce6`)

## Goals

1. Assistant mode: **questions → Groq answer** shown in an **expanded pill** (question + answer).  
2. Assistant mode: **actions** (open/play/YouTube/app/skill) → existing execution cascade.  
3. Fix **critical** bugs that block reliability.  
4. Keep a **backlog** of medium/low issues (not in this pass).

## Non-goals

- Persistent chat history / saving Q&A threads.  
- Softening `TRANSCRIPTION_PROMPT`.  
- Fixing every medium/low audit item.  
- Wayland AT-SPI paste redesign.

## UX — answer pill

- After Groq answers, expand the existing recording pill (same window family) to show **question** and **answer**.  
- Auto-dismiss ~**9s**.  
- **Hover** pauses dismiss; on mouse leave, wait **5s** then dismiss.  
- **X** (or Esc cancel path) dismisses immediately.  
- No status toasts for “Transcribing…” (pill animation is enough).  
- No paste of the answer into the focused app (display only). Ambiguous non-questions may still paste.

## Routing (assistant)

After Whisper:

1. Clear **action** (app / YouTube / site / browser / matched skill) → run that path (Codex only for skills / coding intents).  
2. Clear **question** → Groq `answer()` → answer pill → return.  
3. Ambiguous statement → **paste** like smart dictation.

## Critical fixes (this pass)

| ID | Fix |
|----|-----|
| C1 | Do not overwrite Groq answers with cleanup. |
| C3 | Too-short recording must dismiss the pill. |
| C4 | Pill stop signal must **never** start a new recording when idle. |
| C5 | Cancel during processing resets Groq client; bump token (no silent double workers preferred). |
| C6 | Feedback follows `DeliveryStatus` (no fake success on failed paste). |
| C7 | `cancel()` also cancels delivery; clear cancel latch at session start. |
| Bleed | Reject Whisper prompt regurgitation without changing the prompt. |
| Paste | Ctrl+Shift+V for known terminals **and** Cursor/VS Code terminal focus when detectable. |

## Medium/low backlog (documented, not this pass)

See audit: dual amplitude writers, notify env gaps on non-answer paths, skill false positives, YouTube sync scrape, Codex timeout UX, Wayland Esc, history retention, etc.

## Success criteria

1. Double-press + “Who is the PM of India?” → expanded pill with answer; no Codex.  
2. Double-press + “open YouTube” → opens browser; no Q&A pill.  
3. Accidental assistant + statement → paste, no Q&A toast.  
4. Too-short clip does not leave a stuck pill.  
5. Pill Stop does not ghost-start recording.  
6. Failed paste does not play success.  
7. “English and Hinglish…” bleed does not paste.
