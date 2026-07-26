# Cross-Platform OS Adapters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract Linux desktop I/O behind a `PlatformBundle` contract, then ship macOS and Windows MVPs so dictation + app/site open work on multiple OSes without rewriting Groq/controller/history.

**Architecture:** Shared orchestration (`Controller`, `GroqClient`, `HistoryStore`) stays OS-agnostic. A `build_platform()` factory returns a `PlatformBundle` (audio, hotkeys, delivery, target probe, apps, browser, feedback, settings). Linux code moves under `vaani.platform.linux` with re-exports; Mac/Windows add sibling packages. Spec: [2026-07-26-cross-platform-adapters-design.md](../specs/2026-07-26-cross-platform-adapters-design.md).

**Tech Stack:** Python 3.10+, `httpx`, `keyring`, `platformdirs`; Linux: `python-xlib` + system GTK/PulseAudio; Mac/Windows: `sounddevice`, `pyperclip`, `pynput` (or native hotkey APIs).

**Branch:** `plat/p1-cross-platform`  
**Roadmap IDs:** P1-01 … P1-06

**Progress (2026-07-26):** Package A started on this branch.
`PlatformBundle` / `detect_os` / `build_platform`, per-OS `Settings` roots,
Linux `runtime.py` boot path, and injectable app/browser launchers are in
`26adb87`. Next: Packages C/D (macOS / Windows adapters).

---

## Sub-agent execution map

Run **Package A** to completion first (or as the only serial track). Then launch **C and D in parallel** on separate worktrees/branches. **B** may land inside A. **E** starts after A and finishes after C/D docs exist.

| Agent package | Branch suggestion | Tasks |
|---|---|---|
| A — Contract + Linux | `plat/p1-01-os-adapter` (or this branch) | Tasks 1–6 |
| B — Paths (optional split) | `plat/p1-04-config-paths` | Task 3 (if split from A) |
| C — macOS | `plat/p1-02-macos-adapter` | Tasks 7–9 |
| D — Windows | `plat/p1-03-windows-adapter` | Tasks 10–12 |
| E — Docs + CI | `plat/p1-05-install-ci` | Tasks 13–14 |

**Merge order:** A → (B if separate) → C and D (either order) → E.

---

## File structure (target)

```text
src/vaani/
  platform/
    __init__.py              # detect_os, build_platform
    protocol.py              # PlatformId, FocusSnapshot, Protocols, PlatformBundle
    linux/
      __init__.py
      runtime.py             # X11 event loop + signal wiring
      audio.py               # moved from vaani.audio (Parec)
      hotkeys.py
      target.py              # from x11.py
      delivery.py
      apps.py
      browser.py
      feedback.py
    macos/
      __init__.py
      runtime.py
      audio.py
      hotkeys.py
      target.py
      delivery.py
      apps.py
      browser.py
      feedback.py
    windows/
      __init__.py
      runtime.py
      audio.py
      hotkeys.py
      target.py
      delivery.py
      apps.py
      browser.py
      feedback.py
  config.py                  # platform-aware Settings
  __main__.py                # build_platform().run(...)
  controller.py              # use BrowserLauncher; drop _open_browser OS paths
  # thin re-exports during migration:
  audio.py, hotkeys.py, delivery.py, x11.py, apps.py, feedback.py

docs/install/macos.md
docs/install/windows.md
tests/unit/platform/
  test_protocol_fakes.py
  test_detect_os.py
  test_linux_bundle_imports.py
```

---

## Package A — Contract + Linux extract (P1-01)

### Task 1: Platform protocols and fakes

**Files:**
- Create: `src/vaani/platform/__init__.py`
- Create: `src/vaani/platform/protocol.py`
- Create: `tests/unit/platform/test_protocol_fakes.py`

- [ ] **Step 1: Write failing test for FocusSnapshot equality and a FakeBundle**

```python
from vaani.platform.protocol import FocusSnapshot

def test_focus_snapshot_equality():
    assert FocusSnapshot(token="a") == FocusSnapshot(token="a")
    assert FocusSnapshot(token="a") != FocusSnapshot(token="b")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -q tests/unit/platform/test_protocol_fakes.py::test_focus_snapshot_equality -p no:cacheprovider`

Expected: FAIL (module missing)

- [ ] **Step 3: Implement `protocol.py` with PlatformId, FocusSnapshot, Protocols, PlatformBundle dataclass**

Include Protocols listed in the design spec §4.3. `PlatformBundle.run` can be abstract via a Protocol or a base with `NotImplementedError`.

- [ ] **Step 4: Run test to verify it passes**

- [ ] **Step 5: Commit**

```bash
git add src/vaani/platform tests/unit/platform
git commit -m "$(cat <<'EOF'
feat(platform): add cross-platform adapter protocols

EOF
)"
```

---

### Task 2: `detect_os` + factory stub

**Files:**
- Modify: `src/vaani/platform/__init__.py`
- Create: `tests/unit/platform/test_detect_os.py`

- [ ] **Step 1: Write tests mapping `sys.platform` → PlatformId (`linux*`, `darwin`, `win32`)**

- [ ] **Step 2: Run to fail**

- [ ] **Step 3: Implement `detect_os()`; `build_platform()` raises `UnsupportedPlatform` for unknown; for now only implements Linux by importing a stub that still fails until Task 4**

Prefer:

```python
def build_platform(settings=None):
    os_id = detect_os()
    if os_id is PlatformId.LINUX:
        from vaani.platform.linux.runtime import build_linux
        return build_linux(settings)
    raise UnsupportedPlatform(os_id)
```

- [ ] **Step 4: Tests pass for detect_os; build_platform Linux may skip until Task 4**

- [ ] **Step 5: Commit**

```bash
git commit -m "$(cat <<'EOF'
feat(platform): add detect_os and build_platform entrypoints

EOF
)"
```

---

### Task 3: Platform-aware Settings + amplitude path

**Files:**
- Modify: `src/vaani/config.py`
- Modify: `src/vaani/controller.py` (amplitude path)
- Modify: `tests/unit/test_config.py`
- Optional: add `platformdirs` to `requirements.in` / lock

- [ ] **Step 1: Extend tests so Settings roots differ by platform when `sys.platform` is monkeypatched (or pass an explicit PlatformId into `Settings.from_home`)**

API choice (pick one and stick to it):

```python
Settings.from_home(home=None, *, platform: PlatformId | None = None)
```

- [ ] **Step 2: Implement using `platformdirs` or explicit tables:**
  - Linux: keep `~/.local/share/vaani`, `~/.cache/vaani`, `~/.config/vaani`
  - macOS: Application Support / Caches / Preferences-equivalent config
  - Windows: `%APPDATA%\Vaani`, `%LOCALAPPDATA%\Vaani\Cache`
- [ ] **Step 3: Soften `prepare()` / `sweep_audio_directory` on Windows (`getuid`/`umask` guards)**
- [ ] **Step 4: Change amplitude file from `/tmp/vaani-amplitude` to `settings.cache_dir / "amplitude"` (thread settings or path into controller)**
- [ ] **Step 5: Run `tests/unit/test_config.py` + controller unit tests
- [ ] **Step 6: Commit**

```bash
git commit -m "$(cat <<'EOF'
feat(config): use per-OS data paths and cache amplitude IPC

EOF
)"
```

---

### Task 4: Move Linux implementations under `platform.linux`

**Files:**
- Create: `src/vaani/platform/linux/{audio,hotkeys,target,delivery,apps,browser,feedback,runtime}.py`
- Modify: `src/vaani/{audio,hotkeys,x11,delivery,apps,feedback}.py` → re-export for compatibility
- Modify: `src/vaani/controller.py` to use `BrowserLauncher` instead of `_open_browser`
- Modify: tests imports only if needed (prefer stable re-exports)

- [ ] **Step 1: Move code with `git mv` semantics (copy then re-export) keeping public names working**
- [ ] **Step 2: Implement `build_linux(settings) -> PlatformBundle` in `runtime.py` wiring today’s `__main__` pieces**
- [ ] **Step 3: Extract browser open from `controller._open_browser` into `platform.linux.browser`**
- [ ] **Step 4: Run full unit suite**

Run: `PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -q -p no:cacheprovider tests/unit`

Expected: PASS (same as before extract)

- [ ] **Step 5: Commit**

```bash
git commit -m "$(cat <<'EOF'
refactor(platform): extract Linux desktop I/O behind PlatformBundle

EOF
)"
```

---

### Task 5: Switch `__main__` to platform runtime

**Files:**
- Modify: `src/vaani/__main__.py`
- Modify: `src/vaani/platform/linux/runtime.py`

- [ ] **Step 1: Replace inline X11 bootstrap with `build_platform(settings).run(controller)` pattern**
- [ ] **Step 2: Keep `--record` path working (platform recorder)**
- [ ] **Step 3: Manual smoke on Linux if available: `.venv/bin/python -m vaani --record`**
- [ ] **Step 4: Commit**

```bash
git commit -m "$(cat <<'EOF'
feat(platform): boot Vaani through build_platform on Linux

EOF
)"
```

---

### Task 6: Package A verification gate

- [ ] **Step 1: Run unit tests**
- [ ] **Step 2: Run Linux integration tests that apply (`DISPLAY` present) or note skips**
- [ ] **Step 3: Update ROADMAP P1-01 → `done` or `partial`**
- [ ] **Step 4: Push / open PR for Package A if using a split branch**
- [ ] **Step 5: Commit roadmap status if changed**

**Stop here before Mac/Windows agents start** unless they work in isolated worktrees off this commit.

---

## Package C — macOS adapter (P1-02)

> Agent must run on a Mac (or produce code + mark live checks manual). Do not edit `platform/linux/**` except for shared protocol bugs.

### Task 7: macOS audio + feedback + feedback + delivery

**Files:**
- Create: `src/vaani/platform/macos/{audio,target,delivery,feedback}.py`
- Create: `tests/unit/platform/test_macos_fakes.py` (mocked; skip on non-darwin if importing native libs)

- [ ] **Step 1: Implement `SoundDeviceRecorder` honoring 16 kHz mono WAV + `validate_wav`**
- [ ] **Step 2: Implement clipboard set + Cmd+V paste; focus snapshot via frontmost app token**
- [ ] **Step 3: If Accessibility missing, deliver `CLIPBOARD_ONLY` and notify**
- [ ] **Step 4: Notifications via `osascript` or equivalent; sounds via `afplay` optional**
- [ ] **Step 5: Unit tests with mocks**
- [ ] **Step 6: Commit**

```bash
git commit -m "$(cat <<'EOF'
feat(macos): add audio, focus, delivery, and feedback adapters

EOF
)"
```

---

### Task 8: macOS hotkeys, apps, browser, runtime

**Files:**
- Create: `src/vaani/platform/macos/{hotkeys,apps,browser,runtime}.py`
- Modify: `src/vaani/platform/__init__.py` (`build_platform` → macos)
- Create: `docs/install/macos.md`

- [ ] **Step 1: HotkeyService for smart/literal/assistant + cancel**
- [ ] **Step 2: App catalog using `open -a` (Terminal, Finder, Calculator, System Settings, Visual Studio Code, Cursor, …)**
- [ ] **Step 3: BrowserLauncher for Brave/Chrome/default**
- [ ] **Step 4: `build_macos` + `PlatformBundle.run` (listener thread or loop)**
- [ ] **Step 5: Write `docs/install/macos.md` (permissions + smoke checklist)**
- [ ] **Step 6: Manual checklist on a Mac (record in PR):**
  - [ ] `--record` prints transcript
  - [ ] Smart dictation pastes into TextEdit or browser
  - [ ] “Open Terminal” via assistant mode
  - [ ] Focus-change ⇒ clipboard-only
- [ ] **Step 7: Commit**

```bash
git commit -m "$(cat <<'EOF'
feat(macos): wire PlatformBundle runtime, apps, and install docs

EOF
)"
```

---

### Task 9: macOS packaging extras

**Files:**
- Modify: `pyproject.toml`, `requirements.in` (and regenerate lock or add macos extra notes)

- [ ] **Step 1: Add `macos` optional deps; ensure `python-xlib` not required on darwin**
- [ ] **Step 2: Document `uv pip install -e '.[macos]'` in install doc**
- [ ] **Step 3: Commit**

---

## Package D — Windows adapter (P1-03)

> Mirror Package C. Do not edit macOS or Linux packages except shared protocol.

### Task 10: Windows audio + target + delivery + feedback

**Files:**
- Create: `src/vaani/platform/windows/{audio,target,delivery,feedback}.py`
- Create: `tests/unit/platform/test_windows_fakes.py`

- [ ] **Step 1: `SoundDeviceRecorder` (shared helper with macOS OK if placed in `platform/audio_common.py`)**
- [ ] **Step 2: Clipboard + Ctrl+V `SendInput`; hwnd focus snapshot**
- [ ] **Step 3: Toast/notify fallback; `winsound` optional**
- [ ] **Step 4: Mocked unit tests**
- [ ] **Step 5: Commit**

```bash
git commit -m "$(cat <<'EOF'
feat(windows): add audio, focus, delivery, and feedback adapters

EOF
)"
```

---

### Task 11: Windows hotkeys, apps, browser, runtime

**Files:**
- Create: `src/vaani/platform/windows/{hotkeys,apps,browser,runtime}.py`
- Modify: `src/vaani/platform/__init__.py`
- Create: `docs/install/windows.md`

- [ ] **Step 1: `RegisterHotKey` or `pynput` HotkeyService + message loop in `run()`**
- [ ] **Step 2: App catalog (Windows Terminal, notepad, explorer, calc, code, cursor, …)**
- [ ] **Step 3: BrowserLauncher**
- [ ] **Step 4: `build_windows` + control strategy without SIGUSR (localhost control file OK)**
- [ ] **Step 5: `docs/install/windows.md` + manual checklist in PR**
- [ ] **Step 6: Commit**

```bash
git commit -m "$(cat <<'EOF'
feat(windows): wire PlatformBundle runtime, apps, and install docs

EOF
)"
```

---

### Task 12: Windows process helpers

**Files:**
- Modify: `src/vaani/codex.py` (process-group kill portability)
- Modify: `src/vaani/platform/windows/runtime.py` as needed

- [ ] **Step 1: Abstract cancel/kill so Windows does not call `os.killpg`**
- [ ] **Step 2: Unit test with fakes**
- [ ] **Step 3: Commit**

```bash
git commit -m "$(cat <<'EOF'
fix(codex): make assistant process cancellation work on Windows

EOF
)"
```

---

## Package E — Docs + CI (P1-05, P1-06)

### Task 13: README + ROADMAP status

**Files:**
- Modify: `README.md` (platform banner, link install matrix)
- Modify: `ROADMAP.md` Phase 1 statuses
- Create: `docs/install/README.md` (matrix linking linux/mac/windows)

- [ ] **Step 1: Add install matrix table**
- [ ] **Step 2: Point Important callout at multi-OS support status honestly**
- [ ] **Step 3: Mark P1-* roadmap rows**
- [ ] **Step 4: Commit**

---

### Task 14: CI matrix smoke

**Files:**
- Create or modify: `.github/workflows/ci.yml` (if none exists, add one)

- [ ] **Step 1: Linux job — unit tests**
- [ ] **Step 2: macOS + Windows jobs — unit tests that skip native-only cases**
- [ ] **Step 3: Ensure `python-xlib` install only on Linux job**
- [ ] **Step 4: Commit**

```bash
git commit -m "$(cat <<'EOF'
ci: run unit tests on Linux, macOS, and Windows

EOF
)"
```

---

## Manual acceptance checklist (Phase 1)

Copy into the final PR:

- [ ] Linux: smart dictation paste still works (no regression)
- [ ] macOS: `--record`, paste, open app, clipboard-only on focus change
- [ ] Windows: `--record`, paste, open app, clipboard-only on focus change
- [ ] `pip install` / `uv` on Mac/Win does not require `python-xlib`
- [ ] No credentials or machine-specific paths in the diff
- [ ] ROADMAP Phase 1 updated

---

## Parallelism notes for humans

1. Friend on Phase 0: avoid simultaneous heavy edits to `controller.py`; Package A should extract `_open_browser` early to reduce conflict.
2. After Task 6, Mac and Windows agents only need `platform/protocol.py` + factory hooks stable.
3. Shared `sounddevice` recorder helper belongs in `platform/audio_common.py` if both C and D need it — land in A or a tiny follow-up before C/D diverge.

---

## Spec reference

Full design decisions and risk table:
[docs/superpowers/specs/2026-07-26-cross-platform-adapters-design.md](../specs/2026-07-26-cross-platform-adapters-design.md)
