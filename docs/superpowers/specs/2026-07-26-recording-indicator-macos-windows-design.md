# Recording Indicator — macOS & Windows Design

**Date:** 2026-07-26  
**Status:** Plan / design for implementation  
**Related:** Linux GTK pill in [`src/vaani/indicator.py`](../../../src/vaani/indicator.py)

## 1. Goal

Bring the Linux recording pill UX to **macOS and Windows**:

- Floating always-on-top bar while recording
- Live amplitude / waveform
- **X** → cancel (no delivery)
- **Stop** → stop recording and process
- Drag to reposition; restore position next time

Dictation must keep working if the pill fails to start (headless fallback).

## 2. Why it is missing today

| Platform | Feedback | Indicator |
|---|---|---|
| Linux | `vaani.feedback.Feedback` | Spawns `python -m vaani.indicator` (GTK4) |
| macOS | `MacFeedback` | Sounds + notification only |
| Windows | `WindowsFeedback` | Sounds + toast only |

Phase 1 MVP explicitly deferred the pill. Hotkeys/mic on Mac are now working; the pill is the next UX gap.

## 3. Architecture

Keep the **headless model** (`RecordingIndicator`) shared. Add **per-OS views** as separate processes (same pattern as Linux), so the Vaani daemon’s hotkey loop is not blocked by a UI toolkit run loop.

```text
Vaani daemon (hotkeys / record / Groq)
        │
        ├── writes amplitude → cache_dir/amplitude
        ├── on start cue → spawn indicator process
        ├── on stop/success/fail → terminate indicator
        │
indicator process (macOS AppKit | Windows Win32/Tk)
        ├── reads amplitude file
        ├── polls control replies / sends control requests
        └── X → cancel, Stop → stop
```

### 3.1 Portable control channel (replace SIGUSR-only)

Linux today: indicator → `SIGUSR1` (stop) / `SIGUSR2` (cancel) to parent.

That fails on Windows and is awkward cross-process. Introduce a tiny shared protocol:

**File:** `{cache_dir}/indicator_control.json` (mode 0600)

Daemon writes (optional heartbeat):

```json
{ "pid": 12345, "state": "recording", "updated_ms": 0 }
```

Indicator writes commands:

```json
{ "command": "stop" | "cancel", "ts_ms": 0 }
```

Daemon polls every ~50–100ms while recording/processing **or** watches with a short sleep in the existing loops. After handling, clear `command`.

Keep Linux SIGUSR path as a fast path; add file control for all platforms so Mac/Win share one mechanism. Longer term, Linux can migrate to file-only.

### 3.2 Amplitude

Already portable: `VAANI_AMPLITUDE_PATH` / `settings.cache_dir / "amplitude"`.  
Indicator reads the scalar float; no audio bytes cross the boundary.

### 3.3 UI toolkit choice

| OS | Choice | Why |
|---|---|---|
| macOS | **PyObjC AppKit** `NSPanel` (borderless, floating, nonactivating) | Matches always-on-top overlay; already pulled transitively via pynput stack |
| Windows | **tkinter** (stdlib) borderless topmost `Toplevel` | No extra native GUI dep; good enough for a 240×40 pill |
| Linux | Keep GTK4 | Unchanged |

Do **not** rewrite Linux GTK in this work.

Visual target: match Linux pill (dark capsule, grey X, white level dots/bars, red stop).

## 4. Module layout

```text
src/vaani/
  indicator.py                 # shared model + linux GTK entry (existing)
  indicator_protocol.py        # NEW: control file helpers + paths
  platform/macos/indicator_app.py   # NEW: AppKit pill process
  platform/windows/indicator_app.py # NEW: tkinter pill process
  platform/macos/feedback.py   # spawn/terminate mac indicator
  platform/windows/feedback.py # spawn/terminate win indicator
  controller.py                # optional: poll control file while recording
```

Entry points:

```bash
python -m vaani.platform.macos.indicator_app
python -m vaani.platform.windows.indicator_app
```

(or `python -m vaani.indicator --ui=macos|windows|gtk` — prefer explicit modules for clarity)

## 5. Lifecycle

1. User starts dictation → controller `recording` → feedback `play("start")`
2. Feedback spawns indicator subprocess with env:
   - `VAANI_AMPLITUDE_PATH`
   - `VAANI_INDICATOR_CONTROL`
   - `VAANI_INDICATOR_PARENT_PID` (informational)
3. Pill shows; polls amplitude ~20 Hz; draws waveform
4. User clicks Stop → write `command=stop` → daemon `controller.stop()`
5. User clicks X → write `command=cancel` → daemon `controller.cancel()`
6. On `processing` / `delivered` / `failure` / `cancelled` → feedback terminates indicator

## 6. Non-goals

- Pixel-perfect redesign / branding pass
- In-process AppKit on the same thread as Carbon hotkey pump
- Replacing Linux GTK
- Full settings UI for pill theme

## 7. Acceptance

### macOS

- [ ] Pill appears on ⌃⌥V start, disappears after deliver/cancel
- [ ] Waveform reacts while speaking
- [ ] X cancels; Stop processes
- [ ] Position restored after relaunch
- [ ] Dictation still works if AppKit init fails

### Windows

- [ ] Same behaviors with Windows hotkeys
- [ ] No dependency on Unix signals
- [ ] Works on Win10/11 with tkinter from CPython

### Shared

- [ ] Unit tests for control-file protocol + headless model
- [ ] Docs updated in `docs/install/macos.md` and `windows.md`

## 8. Risks

| Risk | Mitigation |
|---|---|
| AppKit + separate process permission prompts | Document; pill is visual-only |
| tkinter look ugly / DPI | Fixed logical size; scale with `tk` scaling if needed |
| Control-file races | Atomic write (temp + replace); ignore stale `ts_ms` |
| Zombie indicators | Kill on daemon start (platform-specific) + terminate on cues |
