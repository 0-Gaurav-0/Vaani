# Transcript reliability + operator open — phased design

**Date:** 2026-07-28  
**Status:** Approved (phased); **implement Phase 0 first**  
**Branch:** `feat/assistant-qa-pill-critical`  
**Related:** HANDOVER §6, operator-actions plan, ideas I-01…I-07

## Problem

1. **Mic off / silence** → Whisper invents junk (`English Hinglish Thank you`, bare `Examples`).
2. **Prompt bleed** → real speech gets `English.` / `Examples` / `Thank you` glued on; breaks open routing.
3. **Hindi / Hinglish** → every clip forces `language=en`, so Hindi maps to English gibberish (`Eqam MetaVis`, `English magla bude`). User wants **Latin Hinglish only** (no Devanagari paste).
4. **Open gaps** → folders unsupported; apps beyond hardcoded list fail; VS Code often fails because bleed poisons the transcript.
5. **Later operator needs** → mute/volume, user `apps.json`, confirm-gated Codex, computer-use.

## Priority phases (fix-first)

| Phase | Priority | Why this order | Ships |
| --- | --- | --- | --- |
| **0** | P0 | Bad STT poisons every feature | Silence gate, bleed/junk guard, **double STT** → Latin Hinglish |
| **1** | P0 | User can’t open local stuff | Folders, `apps.json`, XDG `.desktop` discovery |
| **2** | P1 | Daily OS controls, catalog-safe | Mute / volume (`pactl` / `playerctl` allowlist) |
| **3** | P1 | Safety before agent feels default | Confirm-gated Codex (pill confirm) |
| **4** | P2 later | Brittle on Linux/Wayland | Computer-use research spike only |

**Rule:** Finish and verify a phase before starting the next. Do not start Phase 4 until 0–3 feel inevitable.

---

## Phase 0 — Transcript reliability + Latin Hinglish

### Goals

- Muted mic / near-silence → **no paste** (quiet cancel, same family as too-short).
- Junk-only Whisper output rejected; edge bleed stripped from real speech.
- Hindi / mix → readable **Latin Hinglish**, not English phonetic garbage, not Devanagari, not Urdu/Arabic script.

### Design

**Silence gate**  
After WAV validate, compute peak (or high-percentile) PCM16 RMS over the file. If below a fixed floor → treat as empty (`AudioError` or controller ignore) → Idle, no Groq call.

**Bleed / junk guard** (extend `guard_transcription`)

- Reject if transcript is only known hallucinations: `thank you`, `thanks for watching`, `english`, `hinglish`, `examples`, short prompt fragments.
- Strip leading/trailing bleed tokens (`English.`, `Hinglish`, `Examples`, `Thank you`, …) from otherwise-real text.
- Keep rejecting full prompt echo (existing behavior).

**Double STT (option D)**

1. Transcribe twice in parallel (or sequential if simpler): once with `language=en` + Latin prompt, once with `language=hi` + Latin/Hinglish prompt (or empty prompt for `hi`).
2. **Score / pick** the candidate that:
   - Survives `guard_transcription`
   - Is not Arabic/Urdu script (`\u0600-\u06FF`)
   - Prefers Latin letters; if Devanagari, run transliterate-to-Latin via cleanup instruction (or a tiny dedicated pass) before paste
3. Never paste Devanagari. Never paste Arabic/Urdu.
4. Latency cost: ~2× Whisper wall time when parallelized on Groq (acceptable for correctness).

**Cleanup**  
Keep light edit; reinforce: prefer Latin Hinglish; never emit Devanagari/Arabic/Urdu.

### Non-goals (Phase 0)

- Changing `TRANSCRIPTION_PROMPT` meaning (still Latin Hinglish); may shorten Examples list if bleed tests require.
- Folders / XDG / mute / Codex confirm.

### Success criteria

1. Near-silent WAV → no history paste of Thank you / English.
2. `"English. What's the weather…"` → `"What's the weather…"`.
3. Hindi utterance → Latin Hinglish words a human can read (not `Eqam MetaVis`-class garbage) in fixture or live check.
4. Existing English dictation still pastes correctly.
5. Unit tests for guard + picker + silence RMS.

---

## Phase 1 — Open folders, apps.json, XDG apps

### Goals

- `open downloads` / `Documents folder kholo` / Home / Desktop / Pictures / Music / Videos / Trash → `xdg-open` (or Nautilus).
- `~/.config/vaani/apps.json` user aliases override / extend catalog.
- Installed apps via `.desktop` Name/GenericName when not in hardcoded list.
- Open order: **hardcoded → apps.json → folder → XDG → site/browse**.

### Design

- `folders.py`: alias → absolute path under `$HOME` / XDG user dirs; `launch_folder` via `xdg-open`.
- `apps.json`: `{ "aliases": { "signal": ["signal-desktop"], ... } }` merged ahead of built-ins.
- XDG index: scan `/usr/share/applications`, `~/.local/share/applications`, snap desktop dir; skip `NoDisplay=true`; launch with `gtk-launch <id>` or parsed `Exec` **without shell**.
- Wire into `controller` fast path and `open` intent.

### Success criteria

- `open vs code` / `open cursor` launch.
- `open downloads` opens folder.
- App present only as `.desktop` resolves by spoken Name.
- Invalid `apps.json` fails closed (log + ignore).

---

## Phase 2 — Mute / volume

### Goals

- “mute”, “unmute”, “volume up/down”, optional “volume 30” via allowlisted argv only (`pactl`, `playerctl` where relevant).
- Fast path: **never** Codex / Groq route for these.

### Non-goals

- Brightness, lock, screenshot (follow-up unless trivial alongside).

### Success criteria

- Mute/unmute toggles default sink; no shell metacharacters; unit tests with mocked runner.

---

## Phase 3 — Confirm-gated Codex

### Goals

- Before Codex / skill agent runs: pill shows short summary + **Confirm / Cancel** (click or speak).
- Timeout abandons without running.
- Catalog open/play/mute never ask confirm.

### Success criteria

- Coding ask → confirm → runs; cancel → no Codex.
- `open Chrome` → no confirm.

---

## Phase 4 — Computer-use (later)

Research spike only: AT-SPI / screenshot→VLM click loops on Linux/Wayland. **Not** core product until Phases 0–3 are solid. Expect privacy chrome (“Using screenshot”) if ever shipped.

---

## Architecture spine (all phases)

```text
Audio
  → silence gate?
  → double STT → pick Latin Hinglish → guard bleed/junk
  → (dictation) cleanup → paste
  → (assistant) fast: mute | app/json | folder | XDG | site | skill
       else Groq route → open/play/qa/… 
       Codex/skill → confirm (Phase 3) → run
```

## Testing

- Unit: `test_prompt_guard`, new silence RMS, STT picker, folders, apps.json, XDG match (fixtures), volume argv.
- Manual smoke after Phase 0–1: mic mute, Hindi phrase, open VS Code, open Downloads.

## Out of scope forever (unless reopened)

- Building OAuth for every SaaS.
- Always-on screen capture every assistant press.
- Softening reliability of `TRANSCRIPTION_PROMPT` into instruction-following chaos.
