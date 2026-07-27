# Vaani Linux — Handover (2026-07-27)

## Repo location — READ THIS FIRST

Canonical path: `/home/gaurav/Gaurav Projects/Vaani/03-REPOSITORIES/control/Vaani-main`
Also reachable at: `/home/gaurav/Vaani/Vaani-main` (a symlink — **same inode, same files**, confirmed via `stat`).

**No git tracks this directory.** The outer repo at
`/home/gaurav/Gaurav Projects/Vaani/03-REPOSITORIES/control` is a docs/planning
repo only (commits like "docs: plan isolated Vaani project migration"); its
`git status` shows `Vaani-main/` as a single collapsed untracked directory —
it was never `git add`-ed, and `Vaani-main` itself has **no `.git` of its
own** (checked: `ls -la Vaani-main/.git` → does not exist). So there is no
git history, no diff, nothing to `git blame`. **Everything in this doc is
the only record of what changed.** File mtimes (`ls -la`) are your only
other signal of what's recent.

Consider running `git init` inside `Vaani-main` and committing the current
state as a baseline before making further changes — right now a `rm -rf` or
bad edit has no safety net.

There's also a **third, older copy** at
`/home/gaurav/Gaurav Projects/Vaani/01-ACTIVE-APP/source` (symlinked from
`.worktrees/vaani-implementation`), pinned at git commit `fe5e86f` — this is
what the system's `vaani-start` autostart script (`~/.local/bin/vaani-start`,
hardcoded `project_dir="/home/gaurav/Gaurav Projects/Vaani/01-ACTIVE-APP/source"`)
launches on GNOME login. **It is a different, older codebase than the one
described below** — does not have the autorepeat fix or the Wayland backend.
If Vaani starts behaving like "the old version" again, check whether
`vaani-start`'s autostart entry (`~/.config/autostart/com.gaurav.vaani.desktop`)
re-launched this old copy — kill it and start the real one manually:
```bash
pkill -9 -f "python -m vaani"   # kills ALL vaani instances, old or new — check none are duplicated
cd "/home/gaurav/Gaurav Projects/Vaani/03-REPOSITORIES/control/Vaani-main"
setsid -f .venv/bin/python -m vaani >/tmp/vaani-run.log 2>&1 < /dev/null
```

## Where to make changes

**Work in `.../03-REPOSITORIES/control/Vaani-main`.** The `01-ACTIVE-APP/source`
copy (git commit `fe5e86f`, described above) is the *old* codebase the
autostart script launches — do not edit it; if anything, it should
eventually be pointed at the real copy or retired, not patched separately.
Editing it will not fix anything the user sees day-to-day once the running
process is the current one, and will just create a second, diverging
version of the truth.

**Safe / expected to touch, for the open problems in this doc:**
- `src/vaani/hotkeys.py` — `HotkeyManager._is_autorepeat()` is the open bug
  (Problem 1). This is the file to instrument/fix.
- `src/vaani/controller.py` — `trigger()`/`handle_hotkey()` (~lines 80-120,
  where `busy` is emitted) for Problem 1's dispatch-side symptom, and
  `deliver()`'s call site (~lines 320-336) for Problem 2 (delivery-status
  masking).
- `src/vaani/groq.py` — if you end up isolating the transcription latency
  to something fixable client-side (timeouts, retry logic), this is where
  the HTTP calls live. Don't touch `_cleanup_too_divergent`'s 85% threshold
  without cause — that's intentional, working-as-designed behavior, not
  part of any open bug.
- `src/vaani/platform/linux/feedback.py` (`_spawn_indicator`/
  `_stop_indicator`) and `src/vaani/indicator_tk.py` — if the widget/hotkey
  desync (symptom 1) traces to the indicator's file-based state protocol
  rather than purely to the autorepeat bug.
- `src/vaani/platform/linux/wayland/` — only if you have a real Wayland
  machine to test against (see Next Steps #4). Don't "fix" things here
  based on guesses; nothing here is confirmed broken, it's confirmed
  *untested* on a real compositor.
- `tests/unit/test_hotkeys.py`, `tests/unit/platform/test_wayland_*.py` —
  add regression tests alongside any fix; existing ones should keep
  passing (run `.venv/bin/python -m pytest -q tests/` before and after).

**Leave alone unless you have a specific, confirmed reason:**
- `src/vaani/x11.py`, `src/vaani/delivery.py` (the X11 `ClipboardDelivery`/
  `XTestPaster`) — not implicated in any open problem; Problem 2's fix is
  in `controller.py`'s call site, not in `delivery.py` itself.
- `src/vaani/audio.py`, `tests/integration/test_audio_process.py` — the one
  pre-existing test failure lives here, confirmed unrelated to anything
  this session touched. Don't spend time on it unless the user asks.
- `src/vaani/platform/macos/`, `src/vaani/platform/windows/` — untouched
  this session, unverified, out of scope for the Linux problems above.
- `.env` — already has a working `GROQ_API_KEY`. Don't regenerate or
  overwrite it without asking; it's the user's real key.
- `requirements.in`/`requirements.lock`/`pyproject.toml`/`uv.lock` — already
  correctly wired for `jeepney` this session. Only touch if adding a new
  dependency; re-run `uv sync --extra linux` *and* then
  `uv pip install -r requirements.lock` if you do (see gotcha below).

## Environment setup already done

- `.env` exists in Vaani-main with `GROQ_API_KEY` set (a real key was pasted
  into this session — treat it as live/sensitive, it's gitignored via
  `.gitignore`'s `.env`/`.env.*` rule so it won't leak into any future commit).
- `.venv/` built via `uv sync --extra linux`; `pytest` etc. installed on top
  via `uv pip install -r requirements.lock` (uv sync alone does NOT install
  pytest — it's only in `requirements.in`/`.lock`, not `pyproject.toml`'s
  managed deps, so `uv sync` will *remove* pytest if you run it after
  editing `pyproject.toml`; re-run `uv pip install -r requirements.lock`
  afterward if that happens).
- Currently running: check with `ps -ef | grep vaani`. As of this doc, pid
  running is whatever the last `setsid -f .venv/bin/python -m vaani` call
  started — **check for duplicates first** (see Problem 1 below), this bit
  us repeatedly this session.

## What existed before this session

A working Linux/X11-only dictation app (`vaani`, entry point
`vaani.__main__:main`), hold-to-talk model:
- `Ctrl+Space` smart dictation, `Ctrl+Shift+Space` literal, `Ctrl+Alt+Space`
  assistant, `Esc` cancel (best-effort, hardcoded keycodes for one reference
  laptop — see README's "Shortcut compatibility warning").
- X11 passive key grabs (`src/vaani/hotkeys.py::HotkeyManager`) + a separate
  `xinput`-polling manager for Esc/alt-assistant
  (`src/vaani/hotkeys.py::XInputHotkeyManager`).
- Clipboard + XTEST synthetic Ctrl+V paste (`src/vaani/delivery.py`), focus
  tracking via `src/vaani/x11.py::X11Probe`.
- Cross-platform adapter scaffolding already existed
  (`src/vaani/platform/protocol.py` defines `HotkeyService`/`TargetProbe`/
  `TextDelivery`/etc. Protocols; `src/vaani/platform/{linux,macos,windows}/`
  each have their own `runtime.py` with a `build_<os>()`/`run_<os>()` pair).
  macOS/Windows backends already existed as "MVP" implementations
  (`platform/macos/`, `platform/windows/`) — **untouched this session**, not
  verified working, take the user's "it worked on Mac" claim as anecdotal,
  not something this session validated.
- Groq Whisper transcription + a "smart cleanup" LLM pass that rejects
  cleanup output that diverges too much from the raw transcript
  (`src/vaani/groq.py::_cleanup_too_divergent`, rejects if <85% of cleaned
  tokens are in the original — aggressive on short utterances, working as
  designed, not a bug).
- Recording pill indicator: GTK4 (`src/vaani/indicator.py`) with a Tk
  fallback (`src/vaani/indicator_tk.py` / `platform/linux/indicator_app.py`),
  run as a **separate subprocess**, communicating via files (amplitude,
  phase, control) — not direct IPC. This subprocess-plus-files design is
  relevant to Problem 3 below.

## What this session changed

### 1. Fixed (and later found to be incomplete) — X11 key autorepeat bug

**Original bug (root cause of "hotkey doesn't work properly" as first
reported):** `HotkeyManager.handle_event` in `src/vaani/hotkeys.py` treated
every X11 `KeyRelease`/`KeyPress` pair for the tracked key as a real
release/press. X11's default key autorepeat (no XKB detectable-autorepeat
enabled) sends synthetic Release+Press pairs every ~25-30ms while a key is
physically held — so holding `Ctrl+Space` made the app flicker
start/stop/start/stop continuously. Visible in the log as hundreds of
`event=busy state=Recording` lines per hold, and a run that sat "Recording"
for 45s before something force-cancelled it.

**Attempted fix:** `HotkeyManager._is_autorepeat()` (added this session,
`src/vaani/hotkeys.py`) peeks the next queued X event on every `KeyRelease`;
if it's a `KeyPress` for the same key with the **identical X server
timestamp** (the signature of an autorepeat-generated pair), it's swallowed
instead of treated as a real release. Uses `display.pending_events()` /
`select.select(fd, timeout)` / `display.next_event()` since this
python-xlib build (0.33) has no `Xlib.ext.xkb` module — so
`XkbSetDetectableAutoRepeat` (the *actually correct* fix every real toolkit
uses) isn't available through this library. Two regression tests added to
`tests/unit/test_hotkeys.py`
(`test_autorepeat_release_press_pair_is_swallowed`,
`test_genuine_release_with_no_pending_press_still_fires`) using a fake
`QueueDisplay` — **these pass, but that's the problem**: they pass against
a synthetic fake queue, not against the real X server's actual timing
behavior.

**Status: NOT actually fixed.** Confirmed by direct log evidence *after*
restarting with this fix live (twice — first with a 5ms peek window, then
again after widening to 30ms): `event=busy state=Recording` spam still
occurs, at the same ~30ms cadence, well after the fixed process started.
Log evidence (from the vaani.log, this session, with the 30ms-window fix
already running since 12:58, spam recurred at 13:01:50-51):
```
13:01:50,347 INFO event=busy state=Recording category=
13:01:50,379 INFO event=busy state=Recording category=
... (continues every ~30ms for over a second)
```
`controller.py:89-91` shows `_emit("busy")` fires specifically when
`trigger()` is called while `state is not AppState.IDLE` — i.e., this
proves `on_trigger` (the X11 hotkey callback) is still being invoked
repeatedly during a single physical key hold. **The peek-based heuristic is
not reliably catching the autorepeat pairs in production**, despite the
mechanism appearing sound on paper (see analysis below) and despite passing
unit tests.

**Where the investigation was cut off:** I was mid-way through re-reading
`controller.py`'s `trigger()`/`handle_hotkey()` methods (lines ~80-120) to
confirm exactly which code path emits `busy`, when the user asked for this
handover instead. I had *not yet* added live debug instrumentation to see
the actual `nxt.time` vs `release_event.time` values `_is_autorepeat()`
computes in a real run — that's the natural next diagnostic step (see
"Next steps" below).

**Hypothesis for why the peek fails in practice** (unverified, worth
checking first): `Xlib.protocol.display.Display.pending_events()` does
call `send_and_recv(recv=True)` internally (confirmed by reading
`.venv/lib/python3.13/site-packages/Xlib/protocol/display.py:238-250`), so
it should pick up already-arrived socket data, and the `select()` fallback
should catch data not yet buffered. The mechanism *should* work given the
autorepeat Release+Press pair is generated back-to-back by the X server.
Possible failure modes not yet checked:
- The XInputHotkeyManager (separate `xinput query-state` polling thread,
  60ms interval, used for Esc/alt-assistant) might be interacting with or
  masking the timing in ways not accounted for.
- GNOME/mutter's specific autorepeat implementation might not actually set
  identical timestamps on the synthetic pair the way assumed (this was
  never verified against a **real** key hold — only against a hand-rolled
  fake `Display` in the unit test). This assumption needs empirical
  verification with real X11 event tracing (e.g., `xev` while holding
  Ctrl+Space) before trusting or further patching the peek logic.
- Under real system load (this dev box: load average ~3.6, several heavy
  processes including Claude Code itself running concurrently), the ~1-5ms
  window between the main loop processing the KeyRelease and checking
  `pending_events()` might occasionally lose the race even if it usually
  wins — but repeated, sustained spam (not occasional) argues against pure
  scheduling jitter as the sole explanation.

### 2. Found, NOT fixed — controller.py silently masks delivery failures

`src/vaani/controller.py:320-336`: after `self.delivery.deliver(final,
snapshot=snapshot)` returns a `DeliveryStatus` (`PASTE_DISPATCHED` /
`CLIPBOARD_ONLY` / `FAILED`), the code **never branches on it** — it always
calls `self._emit("delivered")` and `self._feedback("success")`
unconditionally. Only the SQLite history row (`history.insert(...,
delivery_status=...)`) records the real status; the log line and the
success sound/notification lie about it. This means: if delivery silently
degrades to clipboard-only (e.g., focus changed mid-recording) or outright
fails, the user sees/hears "success" with no indication they need to
manually paste. This was diagnosed as the likely cause of an earlier "log
said delivered, but no text pasted" report, though direct evidence in
that case actually showed the real status *was* `paste_dispatched`
(checked via `history.sqlite3`), so the true cause of that specific
incident was probably a paste that landed in the wrong place (see
`XTestPaster.paste()` in `src/vaani/delivery.py` — no terminal detection
works reliably without a focus check either). **Either way, this
unconditional-success bug is real and still unfixed** — should branch on
`status` and only emit "delivered"/play success feedback for
`PASTE_DISPATCHED`, with a distinct (quieter, or explicitly-worded)
notification for `CLIPBOARD_ONLY`.

### 3. Built — full Wayland backend (new)

New package `src/vaani/platform/linux/wayland/`:
- `clipboard.py` — `WlClipboard` (wl-copy/wl-paste subprocess wrapper),
  `WtypePaster` (synthetic Ctrl+V via `wtype`, only where the compositor
  supports the wlroots virtual-keyboard protocol; raises `WtypeUnavailable`
  cleanly otherwise).
- `delivery.py` — `WaylandClipboardDelivery`: no focus/target tracking is
  possible on Wayland for an unprivileged client, so it always attempts
  paste and downgrades to `CLIPBOARD_ONLY` on any failure (correctly models
  GNOME/KDE Wayland — always clipboard-only — vs Sway/Hyprland — auto-paste
  works — without inventing a new `DeliveryStatus`).
- `portal.py` — thin `jeepney` D-Bus client for the
  `org.freedesktop.portal.GlobalShortcuts` interface (CreateSession /
  BindShortcuts / the Request-Response async pattern all portal calls use).
  **Verified live** against this machine's real D-Bus session bus
  (`jeepney` mechanics — connection, `send_and_get_reply`, `AddMatch` +
  signal filtering — all confirmed working against the actual bus). Caught
  and fixed a real bug this way: raw `router.send_and_get_reply()` does
  **not** raise on an error reply (only the `Proxy` wrapper's `unwrap_msg`
  does) — first draft was blindly treating an error message's body as a
  valid object-path handle. Fixed by routing through `unwrap_msg` +
  `DBusErrorResponse`.
  **However**, the `GlobalShortcuts` interface itself is *not present* on
  this machine's `xdg-desktop-portal` (confirmed via introspection — this
  box runs X11 anyway, so it's moot locally, but it also means the actual
  bind-shortcuts-and-receive-Activated/Deactivated-signals flow has never
  been exercised against a real implementation of that interface). Written
  strictly to the documented spec, but **unverified end-to-end** — needs a
  real test pass on GNOME 45+, KDE Plasma 6+, or a wlroots compositor.
- `hotkeys.py` — `PortalHotkeyManager`, same `on_trigger`/`on_release`
  callback contract as the X11 `HotkeyManager`, so it plugs into the same
  controller wiring. No Esc-cancel equivalent (portal has no 4th shortcut
  requested) — documented as a known gap.
- `runtime.py` — `run_wayland(settings)`, mirrors `_run_x11` structurally;
  main loop is just `time.sleep(0.2)` since the portal listener runs on its
  own thread (mirrors how the Windows backend's `pynput` listener works).

`src/vaani/platform/linux/runtime.py` changes: `run_linux()` is now a 5-line
dispatcher (`_detect_display_backend()` checks `WAYLAND_DISPLAY` then
`XDG_SESSION_TYPE`, defaults to `"x11"`) that calls either
`.wayland.runtime.run_wayland()` or the renamed `_run_x11()` (previously
`run_linux()`, logic otherwise unchanged). `_reap_orphan_indicators()` is
now imported by `wayland/runtime.py` too (shared, not duplicated).

Dependency: `jeepney` (pure-Python D-Bus, no libdbus) added explicitly to
`requirements.in` and `pyproject.toml` (`sys_platform == 'linux'` marker;
was previously only a transitive dep of `keyring`→`secretstorage`, now
imported directly by `wayland/portal.py`).

Docs updated (`README.md`, `docs/install/linux.md`'s X11-vs-Wayland matrix,
`ROADMAP.md`'s P0-06 row) to reflect the new tiered support instead of the
old blanket "Wayland unsupported" claims.

**Tests:** 4 new files under `tests/unit/platform/`
(`test_wayland_clipboard.py`, `test_wayland_delivery.py`,
`test_wayland_hotkeys.py`, `test_linux_runtime.py`), 29 tests total, all
passing, all fully mocked (fake D-Bus router, fake subprocess calls) since
this dev box has no real Wayland session to test against.

**Full suite status:** `165 passed, 1 skipped, 1 failed` — the 1 failure
(`tests/integration/test_audio_process.py::test_normal_sigint_stop_reaps_and_cleanup_deletes`)
is **pre-existing and unrelated**: confirmed neither `audio.py` nor that
test file were touched this session, and it fails deterministically in
isolation regardless of any Wayland/hotkey change (looks like a real
subprocess-timing issue in that test's own SIGINT/reap logic — a real WAV
file race, not something introduced here).

## Currently open problems (as reported by the user, this session ended mid-investigation)

User's exact words: *"The widget is not consistent with the hotkey and
their is a huge delay in transcription, sometimes just doesn't work — Just
know that it worked before for Mac and only struggling on Linux."*

Three symptoms, with what's known about each:

1. **"Widget not consistent with hotkey"** — most likely downstream of the
   still-open autorepeat bug (Problem 1 above): if `on_trigger` fires
   repeatedly during one physical hold, the controller's state machine
   bounces between Recording/blocked-busy rapidly, and the indicator
   subprocess (which learns state via **files** — amplitude path, phase
   path, control path, not direct IPC, see `src/vaani/platform/linux/feedback.py`
   `_spawn_indicator`/`_stop_indicator`) can visibly lag or show a stale
   state relative to the real controller state. Not fully diagnosed —
   plausible primary cause, not confirmed as the *only* cause.

2. **"Huge delay in transcription, sometimes just doesn't work"** — real,
   measured evidence exists for at least one case:
   `event=groq_transcribe_done chars=37 elapsed=6.64` (log line at
   `2026-07-27 13:01:21,278`) — **6.64 seconds** for one Groq Whisper API
   round trip. This is a real HTTP call in `src/vaani/groq.py`; the delay
   is either genuine network/Groq-API latency (this session's dev box has
   a load average of ~3.6 with heavy concurrent processes — Claude Code
   itself, browsers — which could also delay the local HTTP client thread
   getting scheduled) or an actual Groq-side slowdown. **Not yet
   distinguished** which. Since this is the same `groq.py` code path
   regardless of OS/backend, "only struggling on Linux" pointing at this
   specific symptom is puzzling — worth asking the user directly whether
   they mean the transcription API call itself is slow, or whether they
   mean the whole press-to-paste cycle feels slow (which would implicate
   the autorepeat bug's repeated start/stop cycles instead, a
   Linux-hotkey-specific issue that would NOT reproduce on Mac).

3. **"Sometimes just doesn't work"** — no isolated repro captured yet.
   Could be: (a) the autorepeat bug causing a hold to never properly
   register as a single clean cycle, (b) the delivery-status-masking bug
   (Problem 2) making it *look* like nothing happened when actually
   clipboard-only silently occurred, or (c) a stray duplicate vaani
   process again (see below — this happened multiple times this session
   already for unrelated reasons, always check).

**"Worked before on Mac"** — not verified or investigated this session.
The macOS backend (`platform/macos/`) is separate, untouched code. Take
this claim as user-reported context, not a confirmed comparison — the
underlying Groq transcription path is OS-agnostic, so if transcription
itself is slow, it should be similarly slow on Mac too, unless network
conditions differ between machines/locations.

## Known operational gotcha (bit us repeatedly this session, will bite you too)

**Always check for duplicate vaani processes before diagnosing anything:**
```bash
ps -ef | grep -i vaani | grep -v grep
```
Multiple simultaneous instances (each holding its own X11 hotkey grab) was
the root cause of an earlier "hotkey does nothing / does something weird"
report this session, and a backgrounded `Bash` tool call with `timeout N`
was found to NOT reliably kill the wrapped process on timeout — a
"zombie" `uv run vaani` process survived silently for a long time because
a `pgrep` check afterward used a pattern that didn't match its actual
command line (`.venv/bin/vaani`, not `python -m vaani` or `uv run vaani`).
**When killing, verify with an unambiguous pattern**, e.g.:
```bash
pkill -9 -f "python -m vaani"
sleep 1
ps -ef | grep -i vaani | grep -v grep   # must show ONLY the one you just started, or nothing
```

## Suggested next steps, roughly in priority order

1. **Diagnose the autorepeat fix's real-world failure** (Problem 1). Add
   temporary debug logging inside `_is_autorepeat()` in `src/vaani/hotkeys.py`
   to print `nxt.time`, `release_event.time`, and whether
   `pending_events()`/`select()` found anything, then hold `Ctrl+Space` for
   a few seconds and inspect. Also try `xev` (if installed) or
   `xinput test-xi2` while holding the key to see the *actual* raw X11
   event stream and timestamps, independent of this app, to confirm/refute
   the "identical timestamp" assumption the fix relies on. If timestamps
   truly aren't identical or the pairing doesn't work as assumed, the
   peek-based approach may need to be abandoned in favor of a
   time-based heuristic (e.g., "release followed by a press for the same
   key within Nms" without requiring exact timestamp equality) — but that
   trades false-negatives for potential false-positives (swallowing a
   genuine quick tap-release-tap), so needs care.
2. **Fix `controller.py`'s delivery-status masking** (Problem 2) — branch
   on `DeliveryStatus` before emitting "delivered"/playing success
   feedback; give `CLIPBOARD_ONLY` a distinct, honest signal to the user.
3. **Isolate the transcription latency** (Problem 2 in the user report) —
   reproduce with the dev box otherwise idle (no heavy concurrent
   processes) to separate "Groq API is just slow sometimes" from "this
   machine's CPU contention is starving the HTTP client thread."
4. **Validate the Wayland backend for real** — needs an actual GNOME 45+,
   KDE Plasma 6+, or Sway/Hyprland machine. This dev box is X11 and its own
   portal lacks `GlobalShortcuts`, so `platform/linux/wayland/` has only
   been unit-tested with mocks, never run end-to-end.
5. Consider whether `Vaani-main` should get its own `git init` + initial
   commit so future sessions have a real diff/history to work from instead
   of this handover doc being the only record.
