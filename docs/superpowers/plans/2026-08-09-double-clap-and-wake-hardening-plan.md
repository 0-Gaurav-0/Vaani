# Plan: Double clap + wake hardening

**Spec:** `docs/superpowers/specs/2026-08-09-double-clap-and-wake-hardening-design.md`

## Tasks

1. Add testable `DoubleClapGate` (1 impulse → arm, 2 within window → fire, 3+ burst → reject).
2. Wire gate into `WakeListener` snap path (runtime uses this); align snap thresholds with stricter defaults.
3. Update `SnapListener` to the same gate (no quiet-single-fire confirm).
4. Expand `wake_phrase` aliases/prefixes; lower wake end-silence default.
5. Update `VAANI-MAP.md` env notes; run unit tests.
