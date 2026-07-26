# Cross-Platform OS Adapters — Design Spec

**Document date:** 2026-07-26  
**Product:** Vaani  
**Branch:** `plat/p1-cross-platform`  
**Status:** Approved direction for Phase 1 implementation  
**Related:** [ROADMAP.md](../../../ROADMAP.md) Phase 1 (P1-01 … P1-06)

## 1. Purpose

Vaani works today as a personal voice dictation and assistant tool on **Ubuntu
22.04 / GNOME / Xorg**. This spec defines how to keep the existing orchestration
core and add **macOS** and **Windows** backends so the same product behaviors
work on all three platforms.

**North star for Phase 1**

> On at least two of {Linux, macOS, Windows}: say or trigger “Open Terminal /
> Cursor”, and smart-dictate a sentence into a browser — without rewriting the
> controller, Groq client, or history store.

## 2. Goals and non-goals

### 2.1 Goals

1. Introduce a stable **platform adapter contract** above OS APIs.
2. Extract the current Linux/X11 implementation behind that contract with **no
   behavior regression** on Ubuntu/X11.
3. Ship **macOS MVP** and **Windows MVP** that support:
   - microphone capture → Groq transcription
   - smart + literal dictation paste into the focused app
   - global hotkeys for smart / literal / assistant (or documented OS shortcuts)
   - open allowlisted apps and sites/browsers
   - notifications for success/failure
   - system keyring for the Groq key (plus `GROQ_API_KEY` override)
4. Use OS-correct data paths (`platformdirs` or equivalent).
5. Keep unit tests runnable without a GUI; live OS checks remain opt-in.

### 2.2 Non-goals (Phase 1)

- Wayland support (spike only; see ROADMAP P0-06)
- Remote phone control (Phase 3)
- Workflow learning / screen watching
- Full parity of every Linux GNOME app alias on Mac/Windows
- Electron/Tauri rewrite
- Streaming STT, offline Whisper, history UI, settings UI
- Perfect recording-pill UI on Mac/Windows (headless + notifications is OK for MVP)

## 3. Current state (Linux inventory)

Platform-dependent today (must be adapted):

| Capability | Primary modules |
|---|---|
| Global hotkeys | `hotkeys.py`, `__main__.py` X11 loop |
| Audio | `audio.py` (`parec` / `pactl`) |
| Clipboard + paste | `delivery.py` (GTK/`xclip`, XTEST) |
| Focus safety | `x11.py` |
| App launch | `apps.py` (GNOME executables) |
| Browser launch | `controller._open_browser`, `sites.py` path |
| Feedback | `feedback.py` (`paplay`, `notify-send`) |
| Indicator | `indicator.py` (GTK4, `/tmp` amplitude, SIGUSR) |
| Paths | `config.py` (XDG + `getuid`/`umask`) |
| Autostart / IPC | `scripts/*`, `data/*.desktop`, SIGUSR in `__main__.py` |
| Codex kill | `codex.py` (`killpg`) |

Already OS-agnostic (keep above the adapter line):

- `groq.py`, most of `controller.py`, `history.py`, `types.py`, `observability.py`
- `sites.resolve_site` / `PUBLIC_SITES` (path only is XDG)
- `apps.resolve_app` phrase matching (catalog rows are Linux)
- `audio.validate_wav`, `secrets` via `keyring` (+ env override)

Hardest ports: **hotkeys**, **focus-safe paste**, **indicator UI**.  
Fastest wins: **paths**, **keyring messaging**, **browser open**, **notifications**, **audio via a portable backend**.

## 4. Architecture

### 4.1 Layering

```text
__main__ / platform runtime
        │
        ▼
Controller  (unchanged orchestration)
        │
        ├── GroqClient / HistoryStore / CodexRunner
        │
        └── PlatformBundle (constructed per OS)
              ├── AudioRecorder
              ├── HotkeyService
              ├── TargetProbe
              ├── TextDelivery (clipboard + paste)
              ├── AppLauncher
              ├── BrowserLauncher
              ├── Feedback (notify + sounds + optional indicator)
              ├── Paths / Settings
              └── KeyStore (keyring)
```

### 4.2 Package layout

Prefer incremental extraction over a big-bang rename:

```text
src/vaani/
  platform/
    __init__.py          # detect_os(), build_platform()
    protocol.py          # Protocols + dataclasses
    linux/
      audio.py
      hotkeys.py
      delivery.py
      target.py
      apps.py
      browser.py
      feedback.py
      runtime.py
    macos/
      ...
    windows/
      ...
  # Existing modules remain as thin re-exports or move gradually
  controller.py
  groq.py
  history.py
  ...
```

**Migration rule:** Linux behavior must keep working after each merge. It is OK
for `vaani.audio` to re-export `vaani.platform.linux.audio` during the move.

### 4.3 Adapter contract

Exact names can vary; semantics cannot.

```python
class PlatformId(str, Enum):
    LINUX = "linux"
    MACOS = "macos"
    WINDOWS = "windows"

@dataclass(frozen=True)
class FocusSnapshot:
    """Opaque enough for equality; may hold window id / pid / hwnd."""
    token: str

class AudioRecorder(Protocol):
    def start(self) -> AudioResult: ...
    def stop(self) -> AudioResult: ...
    def cleanup(self) -> None: ...

class HotkeyService(Protocol):
    def register(self) -> None: ...
    def unregister(self) -> None: ...
    # Backend either drives callbacks itself (Mac/Win hooks)
    # or participates in a platform event loop (Linux X11).

class TargetProbe(Protocol):
    def snapshot(self) -> FocusSnapshot | None: ...
    def unchanged(self, before: FocusSnapshot) -> bool: ...

class TextDelivery(Protocol):
    def deliver(self, text: str, *, snapshot: FocusSnapshot | None = None) -> DeliveryStatus: ...

class AppLauncher(Protocol):
    def resolve(self, command: str) -> AppTarget | None: ...
    def launch(self, target: AppTarget) -> str: ...

class BrowserLauncher(Protocol):
    def open(self, url: str, *, prefer: str | None = None) -> str: ...

class FeedbackService(Protocol):
    def play(self, cue: str) -> bool: ...
    def notify(self, category: str, message: str = "") -> None: ...

@dataclass
class PlatformBundle:
    id: PlatformId
    settings: Settings
    recorder: AudioRecorder
    hotkeys: HotkeyService
    target: TargetProbe
    delivery: TextDelivery
    apps: AppLauncher
    browser: BrowserLauncher
    feedback: FeedbackService
    key_store: KeyStore
    def run(self, controller: Controller) -> int:
        """Own the process event loop until shutdown."""
        ...
```

`Controller` continues to depend on small injectables. Prefer adapting
`__main__.py` to `build_platform().run(controller)` rather than teaching
`Controller` about `sys.platform`.

### 4.4 Factory

```python
def detect_os() -> PlatformId: ...

def build_platform(settings: Settings | None = None) -> PlatformBundle:
    """Return the concrete bundle for the current OS."""
```

Unsupported combinations (e.g. Linux Wayland in Phase 1) fail at startup with
an actionable message, same spirit as today’s missing `DISPLAY` exit.

## 5. Platform MVP matrices

### 5.1 Shared MVP behaviors

| Behavior | Required |
|---|---|
| Smart dictation hotkey toggle | Yes |
| Literal dictation hotkey toggle | Yes |
| Assistant hotkey toggle | Yes (or documented alternate) |
| Cancel in-flight work | Yes |
| Mic → 16 kHz mono WAV → Groq | Yes |
| Paste into unchanged focused text field | Yes |
| Clipboard-only if focus changed / paste fails | Yes |
| Open allowlisted app by voice (assistant mode) | Yes (≥5 apps) |
| Open allowlisted site / browser | Yes |
| Desktop notification on major outcomes | Yes |
| Keyring or `GROQ_API_KEY` | Yes |
| Autostart | Nice-to-have (P1 follow-up OK) |
| Floating recording pill | Linux keeps GTK; Mac/Win may use notify-only MVP |

### 5.2 Linux (extract, do not regress)

| Concern | Approach |
|---|---|
| Hotkeys | Keep `XGrabKey` + optional `xinput` Esc path behind `HotkeyService` |
| Audio | Keep `parec` implementation |
| Delivery | Keep GTK/`xclip` + XTEST |
| Target | Move `X11Probe` → `platform.linux.target` |
| Apps | Current `APPS` table |
| Runtime | Existing X11 `display.next_event()` loop |

### 5.3 macOS MVP

| Concern | Approach (preferred) | Fallback |
|---|---|---|
| Audio | `sounddevice` (PortAudio) writing WAV | `ffmpeg -f avfoundation` |
| Hotkeys | `pynput` or Quartz event tap | Document manual Accessibility setup |
| Clipboard | `pyperclip` or AppKit `NSPasteboard` | — |
| Paste | Synthetic Cmd+V via Quartz/`pynput` | Clipboard-only mode if Accessibility denied |
| Focus | Frontmost app bundle id / AX focused element token | App-level snapshot only |
| Apps | `open -a "App Name"` catalog | — |
| Browser | `open -a "Brave Browser" URL` / Chrome / default | `webbrowser` |
| Notify | `osascript` display notification or `pync` | Log-only |
| Sounds | `afplay` system sounds | Skip |
| Keyring | `keyring` → Keychain | Env override |
| Paths | `~/Library/Application Support/Vaani`, Caches | via `platformdirs` |
| Permissions docs | Microphone, Accessibility, Input Monitoring | Blocker for paste/hotkeys |

**Default hotkeys on macOS:** keep Ctrl+Space family where possible; if the OS
or browser steals them, document alternatives (e.g. Ctrl+Shift+Space still
literal; Option-based chords) in install docs — do not silently no-op.

### 5.4 Windows MVP

| Concern | Approach (preferred) | Fallback |
|---|---|---|
| Audio | `sounddevice` | `ffmpeg -f dshow` |
| Hotkeys | `ctypes` `RegisterHotKey` message loop | `pynput` |
| Clipboard | Win32 `CF_UNICODETEXT` / `pyperclip` | — |
| Paste | `SendInput` Ctrl+V | Clipboard-only |
| Focus | `GetForegroundWindow` hwnd token | — |
| Apps | `os.startfile` / `ShellExecute` / known `.exe` paths | — |
| Browser | Chrome/Brave paths under Program Files; else `webbrowser` | — |
| Notify | PowerShell toast / tray balloon / `win10toast`-style | Console log |
| Sounds | `winsound` | Skip |
| Keyring | `keyring` → Windows Credential Locker | Env override |
| Paths | `%APPDATA%\Vaani`, `%LOCALAPPDATA%\Vaani\Cache` | via `platformdirs` |
| Process control | Named pipe or localhost control port (no SIGUSR) | — |
| Codex cancel | Process tree kill without `killpg` | — |

## 6. Cross-cutting design decisions

### 6.1 Dependencies

- Keep core deps portable: `httpx`, `keyring`.
- Make `python-xlib` **Linux-only** (extras or environment markers).
- Add portable audio: `sounddevice` (or document system PortAudio) for Mac/Win.
- Optional extras:

```toml
[project.optional-dependencies]
linux = ["python-xlib"]
macos = ["sounddevice", "pyperclip", "pynput"]
windows = ["sounddevice", "pyperclip", "pynput"]
# Exact pins decided in implementation PRs
```

GTK/PyGObject remains a **system** dependency on Linux only.

### 6.2 Paths and file security

Replace hard-coded XDG in `Settings.from_home` with platformdirs-style roots.
On Windows, skip `umask`/`getuid` ownership sweeps; still create private dirs
best-effort. Amplitude IPC must leave `/tmp/vaani-amplitude` and use a path
under `cache_dir` (all OSes).

### 6.3 Control channel (replace signal/pkill long-term)

Linux scripts today use `SIGUSR1/2` and `pkill`. Mac/Windows need another path.

Phase 1 minimum:

- Linux may keep signals.
- Mac/Windows expose a **localhost control socket** or small HTTP control on an
  ephemeral port written to `runtime_dir/control.json` (mode-restricted).

Follow-up (same phase if time): move Linux scripts to the same control channel
so one mental model exists everywhere.

### 6.4 App catalogs

Split phrase resolution (shared) from launch tables (per OS):

```text
resolve_app(command, catalog) -> AppTarget | None
linux_catalog / macos_catalog / windows_catalog
```

MVP catalogs need at least: Terminal, browser-related entries via BrowserLauncher,
VS Code/Cursor if present, file manager, settings, calculator (or OS equivalents).

### 6.5 Browser launch

Move `Controller._open_browser` to `BrowserLauncher`. Controller only decides
*that* a browser intent matched and which URL/prefer flag to pass.

### 6.6 Indicator

- Linux: keep GTK pill.
- Mac/Windows MVP: notifications + optional log amplitude; no blocker if pill
  absent.
- Shared: amplitude file under `settings.cache_dir`.

### 6.7 Testing strategy

| Layer | Runs where |
|---|---|
| Unit tests with fakes | Linux CI + Mac/Win CI |
| Linux live X11/audio | Opt-in on developer X11 machine |
| Mac live Accessibility/mic | Opt-in manual |
| Windows live hotkey/paste | Opt-in manual |

Every adapter method gets a fake used by controller tests. Platform modules own
their unit tests with mocks; do not require Display on Mac CI.

### 6.8 Documentation

Add:

- `docs/install/macos.md`
- `docs/install/windows.md`
- Update README platform banner + link matrix

Each install doc must list: Python version, deps, OS permissions, hotkeys,
smoke commands (`python -m vaani --record`, then foreground hotkey test).

## 7. Sub-agent work packages

Work is split so agents/humans can run in parallel after the contract merges.

| Package | Feature ID | Owner type | Depends on | Deliverable |
|---|---|---|---|---|
| A — Contract + Linux extract | P1-01 | Serial first | — | `platform/protocol.py`, Linux bundle, `__main__` factory, green Linux tests |
| B — Paths | P1-04 | Parallel after A starts if careful | A (or land inside A) | `Settings` via platformdirs; amplitude path fix |
| C — macOS adapter | P1-02 | Parallel | A merged | `platform/macos/*` MVP + install doc |
| D — Windows adapter | P1-03 | Parallel | A merged | `platform/windows/*` MVP + install doc |
| E — Install matrix + CI | P1-05, P1-06 | Parallel | A; docs update when C/D land | CI matrix + README links |

**Conflict rules**

- Only package A may restructure imports of `audio` / `delivery` / `hotkeys` /
  `x11` / `__main__` until A merges.
- C and D must not edit Linux modules except shared protocol defaults.
- App catalog changes for Linux stay in A/B; C/D add their own catalog files.

## 8. Acceptance criteria (Phase 1 done)

1. `detect_os()` + `build_platform()` select a bundle; Linux path unchanged in UX.
2. macOS and Windows each pass the MVP matrix in §5.1 on a real machine
   (manual checklist checked into install docs).
3. Unit test suite passes on Linux CI without Display; Mac/Windows CI run
   pure-python + fakes.
4. `python-xlib` is not required to install Vaani on Mac/Windows.
5. ROADMAP Phase 1 rows P1-01…P1-05 updated to `partial`/`done` as PRs merge.
6. No secrets, private URLs, or machine paths committed.

## 9. Risks

| Risk | Mitigation |
|---|---|
| macOS Accessibility denied → paste/hotkeys fail | Clear notify + clipboard-only fallback; install doc screenshots/steps |
| Hotkey collisions with OS/browser | Document alternates; make chords configurable soon after MVP |
| `sounddevice` binary wheels / PortAudio missing | Prefight error with install hint; optional ffmpeg backend later |
| Large extract PR breaks Linux | Thin re-exports; keep Linux tests green each commit |
| Friend Phase 0 edits `controller.py` | Rebase; browser launch extraction minimizes controller churn |

## 10. Open decisions (resolve in PRs, not blockers)

1. Hotkey library: `pynput` vs native Quartz / `RegisterHotKey` first.
2. Whether Mac/Win MVP ships without floating indicator (default: **yes, notify-only**).
3. Control socket vs keeping Linux signals until Phase 1.5.
4. Exact optional-dependency names and lockfile strategy (`requirements.lock` is
   Linux-oriented today — may need per-OS locks or uv extras).

## 11. Implementation plan

Executable task breakdown:
[2026-07-26-cross-platform-adapters-plan.md](../plans/2026-07-26-cross-platform-adapters-plan.md)
