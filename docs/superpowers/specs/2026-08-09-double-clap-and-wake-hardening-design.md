# Double clap wake + voice wake hardening

**Date:** 2026-08-09  
**Status:** Approved  
**Scope:** Idle mic clap/snap assistant toggle + “hey Vaani” wake listener (Linux)

## Goal

Stop accidental assistant wakes from single mechanical impulses (mic nudge, keyboard chassis, rough Enter). Keep intentional **double clap / double snap**. Make voice wake (“hey/okay Vaani” and STT mangling) more reliable and slightly faster without adding a separate wake-word engine yet.

## Current problems

1. One impulse fires `on_snap` — keyboard hits and mic casing taps look like claps.
2. Voice wake waits ~0.55s trailing silence then full STT; aliases miss common mangling (`bunny`, `rami`, `a vani`).
3. Matching is already case-insensitive; failures are vocabulary / accent / STT spelling, not letter case.

## Behavior

### A. Double clap / double snap (required)

| Event | Result |
|---|---|
| Single impulse (key, chassis, mic tap, one clap) | No activate; arm short window only |
| Second valid clap/snap within window | Activate assistant (same as today’s snap callback) |
| Typing / key-chatter burst | Reject; do not count as double clap |
| Window expires after one impulse | Reset; no action |

- Default second-impulse window: **~0.75s** (env-tunable, e.g. `VAANI_SNAP_DOUBLE_WINDOW_S`).
- First impulse never calls `on_snap`.
- Existing impulse shape checks stay (quiet pad, sharp attack, short decay); optionally slightly stricter abs threshold if soft casing taps still arm too often.
- Keep / reuse tap-burst rejection so rapid keyboard energy does not satisfy “two clean claps”.

### B. Voice wake matching

- Remain **case-insensitive**.
- Expand name aliases for observed STT misses (at least: `bunny`, `rami`, `bani`, plus existing set / edit-distance).
- Accept short junk / filler prefixes before the name, e.g. `a vani`, `eh vani`, `ay bani`, in addition to `hey` / `hi` / `hello` / `ok` / `okay` / …
- Patterns in scope: `hey Vaani`, `okay Vani`, `a Vani`, mangled name forms above.
- **Out of scope for v1:** bare name alone with no prefix (`Vaani` with nothing before it) — reduces false wakes from hearing the name in other speech.
- Payload after wake (command text) unchanged when present; empty payload still means wake-only activate.

### C. Latency

- Reduce trailing end-silence before STT (target ~0.25–0.35s, env-tunable).
- Short wake-only utterances may end sooner once silence is detected (no extra hold for long-command silence budget).
- Still one STT call per candidate utterance (no Porcupine/openWakeWord in this change).

## Non-goals

- New on-device wake-word model
- Disabling clap wake entirely
- Changing TrackPoint hold-to-talk
- Bare-name-only wake (`Vaani` with no prefix)
- TTS / pill UX changes unrelated to wake entry

## Success criteria

1. Rough Enter / chassis hit / single mic nudge does **not** open assistant.
2. Two intentional claps/snaps within the window **does** open assistant.
3. Spoken `hey/okay/a` + Vaani-like name (including bunny/bani/rami-class mangling) wakes more often than today.
4. Typical wake-only phrase feels clearly snappier than the old ~0.55s silence wait (plus same STT cost).
5. Unit tests cover double-impulse gate, single-impulse no-fire, and new wake-phrase aliases/prefixes.

## Implementation touchpoints (expected)

- `src/vaani/snap_listener.py` / `wake_listener.py` — double-impulse state machine
- `src/vaani/wake_phrase.py` — aliases + prefix matching
- `tests/unit/test_snap_listener.py`, `test_wake_phrase.py` (+ wake listener tests if present)
- Env knobs documented in `docs/VAANI-MAP.md` if snap/wake envs are listed there
