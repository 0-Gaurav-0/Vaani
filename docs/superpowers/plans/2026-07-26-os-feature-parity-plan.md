# OS feature parity plan (macOS → Windows + Linux)

Branch: `plat/p1-os-feature-parity`  
Base: `plat/p1-cross-platform` @ `73b60ef`

## Goal

Bring every user-facing capability polished on macOS to **Windows and Linux**
at the same quality bar: hold-to-talk, pill UX (record + processing), mic
lifecycle, input blocking, feedback, and platform trust/setup where applicable.

Shared pipeline pieces (Groq, soft cleanup, compress upload, controller state)
already work on all OS — do not re-implement those.

## Already shared (no port needed)

| Feature | Status |
|---|---|
| Smart / literal / assistant modes | All OS |
| Soft cleanup + fidelity guard | All OS |
| Compress upload (FLAC via soundfile) | All OS (macOS also has afconvert) |
| Processing input block (controller) | All OS |
| Mic open only while recording (PortAudio path) | macOS + Windows; Linux via parec lifecycle |
| History / cancel Esc | All OS |

## Parity backlog (macOS → Win/Linux)

| # | Feature | macOS | Windows gap | Linux gap | Workstream |
|---|---|---|---|---|---|
| 1 | Hold-to-talk (press start / release stop) | Done | Toggle only | Toggle only | Hotkeys |
| 2 | Recording bottom pill (X / wave / ✓) | AppKit + tk | tk present | GTK older layout | UI |
| 3 | Processing-phase pill (dots, stays up) | Done | Done | Pill dismissed on processing | UI |
| 4 | Live waveform from mic RMS | Done | Done | WAV-tail only / weaker | Audio + UI |
| 5 | Orphan pill cleanup on startup | pkill indicator | Missing | pkill GTK only | Runtime |
| 6 | Richer feedback cues | afplay + notify | Generic beep | No processing/paste cues | Feedback |
| 7 | Startup / debug console hints | Present | Thin | Thin | Observability |
| 8 | Trust / permission guidance | `trust.py` helpers | Missing | Missing (X11/Wayland notes) | Security / setup |
| 9 | Native-leaning hotkey stack | Carbon | pynput only | X11 grabs | Hotkeys (optional harden) |
| 10 | Install docs parity | Strong | Partial | Partial | Docs |

## Pipeline

### Step 0 — Done
1. Push current smart-cleanup work on `plat/p1-cross-platform`.
2. Create and push `plat/p1-os-feature-parity`.

### Step 1 — Spec freeze (this doc)
Confirm the backlog above. Prefer **behavior parity** over cloning macOS APIs
(e.g. hold-to-talk on Win/Linux via press/release hooks, not Carbon).

### Step 2 — Windows parity
Order:
1. Hold-to-talk on `windows/hotkeys.py` + `windows/runtime.py` (wire `on_press` / `on_release` like macOS).
2. Orphan indicator cleanup on Windows startup.
3. Feedback cue polish (distinct sounds if available; processing cue).
4. Startup/debug console parity.
5. Permission / setup guidance in `docs/install/windows.md` (+ optional helper module).
6. Manual smoke: hold smart → pill → release → processing dots → paste; block during processing.

### Step 3 — Linux parity
Order:
1. Hold-to-talk: wire release in `linux/runtime.py` / `HotkeyManager` (same semantics as macOS).
2. Processing-phase UI: stop dismissing pill on `processing`; reuse phase protocol + either GTK processing animation or shared tk pill.
3. Live mic RMS into amplitude path (align with `audio_common` or feed controller monitor).
4. Feedback cue parity (processing / paste).
5. Startup/debug + Wayland/X11 trust notes in install docs.
6. Manual smoke on X11 (and note Wayland limits honestly).

### Step 4 — Cross-OS hardening
1. Shared tests for hold-to-talk + processing block (fake hotkey adapters).
2. Update ROADMAP / install matrix.
3. One PR per OS workstream if diff grows large; otherwise single PR from this branch.

## Acceptance (all OS)

- Hold smart chord → pill + waveform → release → processing UI → paste.
- New dictation ignored while processing; Esc cancels.
- Mic not held open while idle.
- Literal skips cleanup; smart uses soft cleanup.
- Install docs document the real chords and UX per OS.

## Non-goals

- Identical key chords across OS (OS conventions differ; document each).
- Cloning Carbon / AppKit onto Win/Linux.
- Changing Groq models or cleanup policy in this branch unless needed for parity bugs.
