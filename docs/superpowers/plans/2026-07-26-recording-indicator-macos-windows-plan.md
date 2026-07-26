# Recording Indicator (macOS + Windows) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a floating recording pill (cancel / waveform / stop) on macOS and Windows that matches Linux UX, wired through existing feedback + amplitude paths.

**Architecture:** Keep the shared headless `RecordingIndicator` model. Spawn a per-OS UI process from `MacFeedback` / `WindowsFeedback` (same lifecycle as Linux GTK). Replace SIGUSR-only cancel/stop with a portable control file so Windows works and Mac/Linux can share one path. Amplitude stays file-based (`cache_dir/amplitude`).

**Tech Stack:** Shared Python protocol; macOS PyObjC AppKit `NSPanel`; Windows stdlib `tkinter` topmost window; existing `pcm16_rms` / amplitude file; pytest for protocol.

**Design:** [`docs/superpowers/specs/2026-07-26-recording-indicator-macos-windows-design.md`](../specs/2026-07-26-recording-indicator-macos-windows-design.md)

---

### Task 1: Portable indicator control protocol

**Files:**
- Create: `src/vaani/indicator_protocol.py`
- Create: `tests/test_indicator_protocol.py`
- Modify: `src/vaani/config.py` (optional helper for control path next to `cache_dir`)

- [ ] **Step 1: Write failing tests for read/write/clear**

```python
def test_write_and_read_command(tmp_path):
    from vaani.indicator_protocol import write_command, read_command, clear_command
    path = tmp_path / "indicator_control.json"
    write_command(path, "stop")
    assert read_command(path) == "stop"
    clear_command(path)
    assert read_command(path) is None

def test_reject_unknown_command(tmp_path):
    from vaani.indicator_protocol import write_command
    path = tmp_path / "indicator_control.json"
    try:
        write_command(path, "explode")
        assert False, "expected ValueError"
    except ValueError:
        pass
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
pytest tests/test_indicator_protocol.py -v
```

- [ ] **Step 3: Implement `indicator_protocol.py`**

API sketch:

```python
ALLOWED = {"stop", "cancel"}

def control_path(cache_dir: Path) -> Path: ...
def write_command(path: Path, command: str) -> None: ...  # atomic replace
def read_command(path: Path) -> str | None: ...
def clear_command(path: Path) -> None: ...
def write_state(path: Path, *, pid: int, state: str) -> None: ...  # optional
```

- [ ] **Step 4: Re-run tests — expect PASS**

```bash
pytest tests/test_indicator_protocol.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/vaani/indicator_protocol.py tests/test_indicator_protocol.py
git commit -m "$(cat <<'EOF'
Add portable recording-indicator control-file protocol.

EOF
)"
```

---

### Task 2: Daemon polls control file while recording

**Files:**
- Modify: `src/vaani/controller.py` (or platform runtime record loop — prefer one shared hook)
- Modify: `src/vaani/indicator.py` (`dispatch_control` — also write control file for Linux parity)
- Test: `tests/test_controller_indicator_control.py` (or extend existing controller tests)

- [ ] **Step 1: Add a small poll helper on the controller / record path**

While state is `recording` (and optionally `processing` for cancel-only), every ~50–100ms:

```python
cmd = read_command(settings.indicator_control_path)  # or cache_dir / "indicator_control.json"
if cmd == "stop":
    clear_command(...); self.stop()
elif cmd == "cancel":
    clear_command(...); self.cancel()
```

Wire this where amplitude is already sampled/written so no new thread is required if a tick already exists; otherwise a short daemon thread is fine.

- [ ] **Step 2: Update Linux `dispatch_control` to write the control file as well as SIGUSR**

So one indicator path works everywhere.

- [ ] **Step 3: Test with a fake control file + fake controller methods**

- [ ] **Step 4: Commit**

```bash
git commit -m "$(cat <<'EOF'
Poll indicator control file for stop/cancel across platforms.

EOF
)"
```

---

### Task 3: macOS AppKit pill process

**Files:**
- Create: `src/vaani/platform/macos/indicator_app.py`
- Create: `tests/test_macos_indicator_app_smoke.py` (import/headless skip if no AppKit)
- Modify: `docs/install/macos.md` (note: pill appears while recording; optional `pyobjc-framework-Cocoa` if not present)

- [ ] **Step 1: Implement borderless floating `NSPanel`**

Requirements:
- Always on top, nonactivating if possible
- Size ~240×40, rounded dark capsule
- Left: X (cancel) → `write_command(..., "cancel")` then quit
- Right: red stop → `write_command(..., "stop")` then quit
- Center: 16-bar waveform from amplitude file (`VAANI_AMPLITUDE_PATH`)
- Drag to move; persist `x,y` under Application Support (`indicator.json`)
- Timer ~50ms to redraw from amplitude
- Exit cleanly if amplitude path missing (still show idle bars)

Use PyObjC:

```python
from AppKit import NSApplication, NSPanel, NSColor, NSBezierPath, ...
```

- [ ] **Step 2: Add `__main__` so it runs as**

```bash
python -m vaani.platform.macos.indicator_app
```

- [ ] **Step 3: Manual smoke on Mac (developer machine)**

Start Vaani with `--debug`, press ⌃⌥V, confirm pill + cancel/stop.

- [ ] **Step 4: Commit**

```bash
git commit -m "$(cat <<'EOF'
Add macOS AppKit recording indicator process.

EOF
)"
```

---

### Task 4: Wire `MacFeedback` to spawn/terminate the pill

**Files:**
- Modify: `src/vaani/platform/macos/feedback.py`
- Modify: `src/vaani/platform/macos/runtime.py` if env paths need injecting

- [ ] **Step 1: Mirror Linux `Feedback.play` lifecycle**

On `cue == "start"` and no indicator process:

```python
env = os.environ.copy()
env["VAANI_AMPLITUDE_PATH"] = str(amplitude_path)
env["VAANI_INDICATOR_CONTROL"] = str(control_path)
self.indicator = subprocess.Popen(
    [sys.executable, "-m", "vaani.platform.macos.indicator_app"],
    env=env,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
```

On `success` / `failure` / `busy` / `processing` / paste-complete cues: `terminate()` and clear handle.

- [ ] **Step 2: Best-effort only — exceptions must not break dictation**

- [ ] **Step 3: Manual verify full cycle: start → speak → stop (pill or hotkey) → deliver**

- [ ] **Step 4: Commit**

```bash
git commit -m "$(cat <<'EOF'
Spawn macOS recording pill from MacFeedback start cue.

EOF
)"
```

---

### Task 5: Windows tkinter pill process

**Files:**
- Create: `src/vaani/platform/windows/indicator_app.py`
- Create: `tests/test_windows_indicator_app_smoke.py` (skip if no display / not win32)
- Modify: `docs/install/windows.md`

- [ ] **Step 1: Implement borderless topmost `tk.Toplevel`**

Same layout contract as Mac/Linux:
- `overrideredirect(True)`, `-topmost`
- Canvas draw: dark pill, X, waveform, red stop
- Poll amplitude file; write control commands on click
- Persist position under `%LOCALAPPDATA%/Vaani/indicator.json`
- No Unix signals

- [ ] **Step 2: Entry point**

```bash
python -m vaani.platform.windows.indicator_app
```

- [ ] **Step 3: Smoke on Windows (or wine/skip in CI with `@pytest.mark.skipif(sys.platform != "win32")`)**

- [ ] **Step 4: Commit**

```bash
git commit -m "$(cat <<'EOF'
Add Windows tkinter recording indicator process.

EOF
)"
```

---

### Task 6: Wire `WindowsFeedback` + amplitude publish check

**Files:**
- Modify: `src/vaani/platform/windows/feedback.py`
- Verify: Windows audio path still writes amplitude file during record (same as Linux/Mac)

- [ ] **Step 1: Spawn `vaani.platform.windows.indicator_app` on start; terminate on terminal cues**

- [ ] **Step 2: Confirm Windows recorder publishes RMS to amplitude path (fix if missing)**

- [ ] **Step 3: Commit**

```bash
git commit -m "$(cat <<'EOF'
Spawn Windows recording pill from WindowsFeedback.

EOF
)"
```

---

### Task 7: Docs, ROADMAP, install notes

**Files:**
- Modify: `ROADMAP.md` (add `p1-04-recording-indicator` or mark complete under Phase 1 UX)
- Modify: `docs/install/macos.md`
- Modify: `docs/install/windows.md`
- Modify: `docs/install/README.md` if feature matrix exists

- [ ] **Step 1: Document pill behavior + troubleshooting**

  - Pill missing → check PyObjC / tkinter; dictation still works
  - Stuck pill → quit Vaani / kill orphan python indicator process

- [ ] **Step 2: Commit**

```bash
git commit -m "$(cat <<'EOF'
Document macOS/Windows recording indicator in install guides.

EOF
)"
```

---

### Task 8: End-to-end acceptance (human)

**macOS checklist**

- [ ] ⌃⌥V shows pill; waveform moves while speaking
- [ ] Pill Stop → transcript delivered
- [ ] Pill X → cancelled, nothing pasted
- [ ] Hotkey Esc still cancels with pill open
- [ ] Pill position restored after quit/relaunch
- [ ] Kill AppKit module (rename temporarily) → dictation still works

**Windows checklist**

- [ ] Hotkey start shows pill
- [ ] Stop / Cancel work without SIGUSR
- [ ] Toast/sounds still fire; pill disappears after delivery

---

## Parallelism notes

| Track | Owner | Depends on |
|---|---|---|
| Task 1–2 (protocol + daemon poll) | Either | None |
| Task 3–4 (macOS UI + wire) | Mac machine | Task 1–2 |
| Task 5–6 (Windows UI + wire) | Windows machine | Task 1–2 |
| Task 7 docs | Either | After UI lands |

Friend can take Windows track while you take macOS after Task 2 merges.

## Out of scope

- Redesigning Linux GTK pill
- Packaging as `.app` / `.msi` tray host
- Theme / accessibility overhaul
