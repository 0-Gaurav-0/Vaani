# Vaani Linux Dictation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Execute sequential checkbox tasks with a fresh implementer and independent reviewer per task.

**Goal:** Deliver the approved Ubuntu 22.04 GNOME/X11 Vaani personal dictation application.

**Architecture:** GTK main context owns state and all GTK/X11 clipboard operations. Typed adapters isolate `hotkeys.py` (XGrabKey), `x11.py` (target probes/XTEST), `audio.py`, `groq.py`, `secrets.py`, `history.py`, `feedback.py`, `observability.py`, and `delivery.py`; `controller.py` orchestrates cancellable operation-id workers. No provider framework, Wayland, context inspection, or retained audio.

**Tech Stack:** Python 3.10, system PyGObject GTK3, httpx, keyring Secret Service, pynput/python-xlib, SQLite, pytest, uv lockfile, PulseAudio `parec`, XTEST.

---

## Global Constraints

- Ubuntu 22.04 LTS, GNOME/X11, user-level install only; exact shortcuts `Shift+Super+R` and `Ctrl+Shift+Super+R` with lock variants, repeat suppression, and release-cycle matching.
- Groq only: `whisper-large-v3-turbo` transcription and `openai/gpt-oss-120b` smart cleanup; exact payloads, parsing, 5/30/120/150/60/75/5-second timeout budgets, bounded retries, cancellation, and raw fallback from the approved spec.
- `parec` exact WAV invocation, secure 0700/0600 audio paths, 250ms–10min and <=25MB validation, SIGINT/SIGTERM/SIGKILL escalation, deletion on every path, and startup sweep of only user-owned regular non-symlink files before workers start.
- States exactly `Idle`, `Recording`, `Processing`; GTK sole writer; operation-id invalidation suppresses late effects. Forced shutdown: invalidate token, unregister/cancel, stop/reap children, close transport/clipboard/database, join workers up to 5s; daemon HTTP workers may remain blocked, callbacks permanently no-op, sanitized forced-shutdown log flushes, no further joins, process exits.
- Target safety probes `_NET_ACTIVE_WINDOW` and `XGetInputFocus` before clipboard replacement and paste; GTK clipboard owner retained through bounded grace/readback; target failure means clipboard-only; never restore prior clipboard or synthesize held-key releases.
- Secret Service only (`service=vaani`, account `groq`), runtime non-empty `GROQ_API_KEY` precedence, no key/transcript/audio in logs, reports, args, env, source, SQLite, or Git.
- SQLite serialized writes, separate short-lived read connections, 2s busy timeout; UTC `YYYY-MM-DDTHH:MM:SS.sssZ`, Unicode casefold search, newest-first history.
- No tray/floating UI, offline/streaming, Wayland, provider abstraction, billing, cloud history, context awareness, or scope expansion.

## Dependency Graph and Module Contract

`types/config/observability` → `secrets/audio/groq/history/hotkeys/x11/feedback` → `delivery` → `controller` → `ui/app` → `installer`. Every adapter is a Protocol in `types.py`; workers return immutable results and never touch GTK. Required artifacts include `requirements.in`, checked-in `requirements.lock`, `data/install-manifest.json`, `README.md`, desktop/autostart files, and all named fixtures below.

### Task 1: Package, types, config, observability, and secure startup sweep

**Files:** `pyproject.toml`, `requirements.in`, `requirements.lock` (verify only), `src/vaani/{__main__,config,types,observability}.py`, `tests/unit/test_types.py`, `tests/unit/test_config.py`, `tests/unit/test_observability.py`, `tests/fixtures/canary.txt`.

**Inputs/outputs:** consumes approved constants; produces typed protocols, named timeout constants, secure paths, logger and `Settings` used by all later tasks.

- [ ] TDD tests assert enum/dataclass signatures, timeout mapping, umask 077, directory/file modes, startup timing before worker creation, sweep deletes only user-owned regular files, rejects symlinks/foreign owners, and repairs chmod.
- [ ] Implement rotating logs (1 MiB + 3 backups), sanitized exception categories, debug-only stderr, allowlisted child env excluding key-like variables, and canary capture across logs/stderr/notifications/args/env/reports/git diff.
- [ ] Run `pytest tests/unit/test_types.py tests/unit/test_config.py tests/unit/test_observability.py -q` (PASS); inject fake key/transcript and run `rg` scan (PASS: no matches); commit `chore: scaffold typed secure runtime`.

### Task 2: Secret Service and exact key-validation boundary

**Files:** `src/vaani/secrets.py`, `tests/unit/test_secrets.py`, `tests/integration/test_keyring_probe.py`.

**Inputs/outputs:** consumes `GroqModelSettings`/sanitized logger; produces `KeyStore` and effective-key snapshot.

- [ ] Test override precedence, masked dialog state, locked/unavailable classification, disabled edits during active states, attributes/labels without key material, and delete-on-all-exit-paths using a fake backend.
- [ ] Implement keyring service/account and authenticated `GET /openai/v1/models` validation (transcription required, cleanup absence separately reported), never disk fallback.
- [ ] Run unit PASS; `dbus-run-session -- bash -c 'VAANI_KEYRING_PROBE=1 pytest tests/integration/test_keyring_probe.py -q'` PASS with unique set/get/delete; commit `feat: add Secret Service key handling`.

### Task 3: Audio recorder and deterministic preflight

**Files:** `src/vaani/audio.py`, `tests/unit/test_audio.py`, `tests/integration/test_audio_process.py`, `tests/fixtures/valid.wav`.

**Inputs/outputs:** consumes config/env allowlist; produces validated `AudioResult` and cleanup hooks.

- [ ] Test exact no-shell argv, secure tempfile, ownership/symlink sweep (owned by Task 1 but exercised at startup), WAV limits, normal SIGINT and escalation deletion/reap, idempotent stop.
- [ ] Implement `parec` lifecycle and preflight functions: `parec --help` exit 0; `parec --list-file-formats` exit 0 and contains `wav`; `pactl get-default-source` exit 0 and non-empty; fake open failure returns exact source error; preflight never records.
- [ ] Run unit/integration PASS with fake executable; `bash -n` not applicable; commit `feat: capture secure validated audio`.

### Task 4: Groq HTTP, fixtures, retries, and redaction

**Files:** `src/vaani/groq.py`, `tests/unit/test_groq.py`, `tests/integration/test_groq_http.py`, `tests/live/test_groq_live.py`, `tests/fixtures/groq/{models_ok.json,models_no_cleanup.json,english.json,hindi.json,hinglish.json,cleanup_fallback.json,malformed.json}`.

**Inputs/outputs:** consumes key snapshot/audio; produces transcript/cleanup typed results.

- [ ] Assert exact GET `/models`, multipart Whisper model/temperature/verbose_json, cleanup model/instruction/max tokens/temperature, named English/Hindi/Hinglish predicates, malformed/truncation/quote/fence fallback, and model absence notification.
- [ ] Test fake-clock deadlines including retry delay, cancellation before second request, 5xx/429 rules, and sanitized body/header/exception logging; no request after cancellation.
- [ ] Implement httpx transport with exact budgets and immutable settings. Run `pytest ... -q` PASS; live suite only with `VAANI_LIVE=1` and existing runtime key; commit `feat: integrate Groq safely`.

### Task 5: SQLite history concurrency

**Files:** `src/vaani/history.py`, `tests/unit/test_history.py`, `tests/integration/test_history_concurrency.py`.

**Inputs/outputs:** consumes `HistoryEntry`; produces CRUD/search store.

- [ ] Test schema excludes keys/audio, UTC format, casefold search, newest ordering, atomic insert, delete/clear snapshot, permissions, serialized write queue, separate short-lived read connections, and 2-second busy timeout under lock.
- [ ] Implement one writer connection guarded by lock/queue and per-read connections with `busy_timeout=2000`; corruption disables history non-destructively.
- [ ] Run both test files PASS; commit `feat: add concurrent-safe SQLite history`.

### Task 6: Hotkeys and X11 probes

**Files:** `src/vaani/hotkeys.py`, `src/vaani/x11.py`, `tests/unit/test_hotkeys.py`, `tests/unit/test_x11.py`, `tests/integration/test_x11_live.py`.

**Inputs/outputs:** consumes callbacks; produces exact mode events and `TargetSnapshot`.

- [ ] Test XGrabKey lock variants/BadAccess via XSync, exact modifier rejection, repeat suppression, release cycles, active-window/focus lookup failures and changes.
- [ ] Implement passive root grabs and target probes; live command `xvfb-run -a dbus-run-session pytest tests/integration/test_x11_live.py -q` expects XTEST present, grabs/paste checks PASS or deterministic BadAccess failure.
- [ ] Commit `feat: implement exact X11 hotkeys and probes`.

### Task 7: Clipboard/XTEST delivery and feedback

**Files:** `src/vaani/delivery.py`, `src/vaani/feedback.py`, `tests/unit/test_delivery.py`, `tests/unit/test_feedback.py`, `tests/integration/test_clipboard_owner.py`.

**Inputs/outputs:** consumes target snapshot/text; produces `DeliveryStatus`; feedback is side-effect-only.

- [ ] Test GTK clipboard owner reference retained on main context through 2s grace, readback deadline, owner-loss classification (`failed` vs `clipboard_only`), shutdown cancellation, no prior clipboard restore, held-key timeout/no releases, and XTEST dispatch.
- [ ] Implement owner lifetime/readback and cancellation; feedback tries `paplay` with allowlisted env, then canberra/GTK beep; actionable categories (key, mic, Groq, quota, cleanup, target, paste, shortcut), degradation cannot alter result.
- [ ] Run unit PASS and `xvfb-run -a dbus-run-session pytest tests/integration/test_clipboard_owner.py -q` PASS; commit `feat: deliver clipboard text and feedback`.

### Task 8: Controller and forced-shutdown state machine

**Files:** `src/vaani/controller.py`, `tests/unit/test_controller.py`, `tests/integration/test_shutdown_unresponsive.py`.

**Inputs/outputs:** consumes all adapters; produces state/events/history/delivery.

- [ ] Test every transition, smart/literal, busy, max-duration capture immediately before idempotent stop, stale suppression, one history insert, degraded feedback, and all failure outcomes.
- [ ] Implement numbered shutdown: invalidate token; unregister/cancel; stop/reap `parec`/sound; close transport; cancel delivery; close DB; join <=5s; mark blocked daemon HTTP worker forced, flush sanitized log, perform no further joins, exit. Add unresponsive mock server test asserting bounded exit and no late side effects.
- [ ] Run `pytest tests/unit/test_controller.py tests/integration/test_shutdown_unresponsive.py -q` PASS; commit `feat: orchestrate cancellable lifecycle`.

### Task 9: GTK application, UI, and lifecycle

**Files:** `src/vaani/ui.py`, `src/vaani/app.py`, `tests/unit/test_ui.py`, `tests/integration/test_app_lifecycle.py`.

**Inputs/outputs:** consumes controller/history/secrets/feedback; produces GTK windows and CLI routing.

- [ ] Test `Gtk.Application` ID, `HANDLES_COMMAND_LINE`, exactly one `hold()`, primary/remote background behavior, visible-window preservation, close-hides without quit/hold release, and quit exactly once; test masked key/history actions.
- [ ] Implement small history window, search/details/actions, key dialog, hidden background, single-instance activation, and GTK-main-context ownership.
- [ ] Run `xvfb-run -a dbus-run-session pytest tests/integration/test_app_lifecycle.py -q` PASS; commit `feat: add GTK app lifecycle and history UI`.

### Task 10: Installer, manifest, rollback, and desktop validation

**Files:** `scripts/install.sh`, `scripts/uninstall.sh`, `scripts/vaani`, `data/vaani.desktop`, `data/vaani-autostart.desktop`, `data/install-manifest.json`, `tests/integration/test_install.py`, `README.md`.

**Inputs/outputs:** consumes lock/package and preflight adapters; produces exact user-level install and manifest-driven removal.

- [ ] Test temp-HOME `--no-start`, exact manifest paths/no wildcards/symlink refusal, mode bits/Exec paths, idempotent reinstall preserving history/key, `requirements.in/lock` presence, uv version/Python>=3.10/GTK import, `uv pip sync --require-hashes` with network disabled/cache incomplete fail-closed, `parec`/pactl expected outcomes, XTEST and temporary Secret Service probes, DISPLAY/X11 refusal, paplay warning, and shell `bash -n`/`shellcheck` when available.
- [ ] Fault-inject every staging/self-check/atomic-rename point: old venv remains at `.venv.previous`, staging removed, wrapper failure restores old; interrupted rename and clean-stop timeout (8s) fail nonzero. Purge confirms exact paths, deletes key before data, and key-delete failure retains data.
- [ ] Implement uv bootstrap/version check (user `~/.local/bin/uv`), `uv venv --system-site-packages`, hash-locked install, import verification of every module, atomic switch and launcher/autostart. Run `pytest tests/integration/test_install.py -q` PASS and `bash -n scripts/*.sh` PASS; commit `feat: add user-level installer and manifest`.

## Test/Evidence Matrix

| Area | Evidence |
|---|---|
| Privacy/logging | observability canary, 1MiB/3 backups, repo/report scans |
| Audio | fake process tests + real preflight/smoke, empty audio dir |
| Groq | exact offline mock fixtures + opt-in live predicates |
| X11/delivery | unit + Xvfb/XTEST clipboard-owner and target-change tests |
| History | CRUD + concurrent lock/busy-timeout integration |
| UI/shutdown | GTK lifecycle + unresponsive-server bounded exit |
| Install | temp-HOME offline hash/rollback/purge matrix + target smoke |

## Subagent Workflow and Review Gates

For each sequential task: generate `scripts/task-brief PLAN N`, dispatch one explicit-model implementer (TDD, tests, commit, report), generate `scripts/review-package BASE HEAD`, dispatch independent reviewer with contracts/global constraints, fix and re-review Critical/Important findings, then append completion to `.superpowers/sdd/progress.md`. After Task 10 run final whole-branch review from `git merge-base`. Never parallelize implementers, skip reports, or expand scope.

## Self-review

All approved-spec sections map to Tasks 1–10; module ownership, lockfile and manifest are explicit; startup sweep, logging canaries, history concurrency, clipboard lifetime, feedback fallbacks, Groq fixtures/deadlines, GTK lifecycle, installer probes/rollback, and forced-shutdown observables have concrete tests and commands. No placeholder wording or undefined interfaces remains.
