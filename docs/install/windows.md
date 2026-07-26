# Vaani on Windows

Windows support for microphone capture, hold-to-talk global hotkeys, clipboard
paste, assistant app/browser launch, and notifications.

## Requirements

- Windows 10 or 11
- Python 3.11+
- [uv](https://github.com/astral-sh/uv) (recommended) or pip
- A working microphone
- Groq API key (`GROQ_API_KEY` or `vaani` keyring entry)
- Optional: [Codex CLI](https://github.com/openai/codex) on `PATH` for assistant answers

## Install

From a checkout of Vaani:

```powershell
uv pip install -e ".[windows]"
```

Or with pip:

```powershell
python -m pip install -e ".[windows]"
```

Optional dependencies pull in `sounddevice`, `pyperclip`, and `pynput`.

## Configure

```powershell
$env:GROQ_API_KEY = "gsk_..."
```

Data lands under `%APPDATA%\Vaani` and cache under `%LOCALAPPDATA%\Vaani\Cache`
(including the amplitude IPC file used by the controller).

## Permissions / setup

1. **Microphone** — Settings → Privacy & security → Microphone: allow access
   and allow desktop apps.
2. Run from a **normal (non-elevated)** user session. Elevated / UIPI-protected
   windows may ignore synthetic Ctrl+V; text stays on the clipboard
   (`CLIPBOARD_ONLY`).
3. Antivirus or accessibility tooling may block `pynput` global hooks — allow
   the Python binary printed at startup (`hotkey interpreter paths` in the log).

Startup prints the log path, debug flag, and a short hold-to-talk hint. Use
`--debug` / `VAANI_DEBUG=1` for hotkey press/release lines.

## Recording pill

While recording, a compact black pill sits at the **bottom** of the screen:
grey **X** (cancel), live waveform, white **✓** (stop & paste). Horizontal drag
only. Spawned via `python -m vaani.platform.windows.indicator_app` (tkinter).

On chord **release**, the same pill switches to a **processing** animation
(pulsing dots) until paste finishes, then vanishes. New dictation hotkeys are
ignored while processing (Esc still cancels). The microphone is opened only
while recording — not for the whole time Vaani is running.

| IPC | Path / env |
|---|---|
| Amplitude | `%LOCALAPPDATA%\Vaani\Cache\amplitude` (`VAANI_AMPLITUDE_PATH`) |
| Stop/cancel control | `%LOCALAPPDATA%\Vaani\Cache\indicator_control.json` (`VAANI_INDICATOR_CONTROL`) |
| Phase (recording/processing) | `%LOCALAPPDATA%\Vaani\Cache\indicator_phase` (`VAANI_INDICATOR_PHASE`) |
| Pill X position | `%LOCALAPPDATA%\Vaani\indicator.json` |

On startup Vaani reaps orphan indicator processes from a prior crash. If a pill
is still stuck: quit Vaani, or end the orphan
`python -m vaani.platform.windows.indicator_app` process in Task Manager.

## Hotkeys

Windows uses **hold-to-talk** (press starts, release stops). At startup you
should see `Vaani hotkeys ready (hold-to-talk):`.

| Chord | Action |
|---|---|
| Hold `Ctrl+Space` | Smart dictation (release = stop → processing → paste) |
| Hold `Ctrl+Shift+Space` | Literal dictation |
| Hold `Ctrl+Alt+Space` | Assistant mode |
| `Esc` | Cancel in-flight recording / request |

If a chord is already claimed by another app, Vaani cannot steal it — free the
shortcut or change the other app’s binding.

### Debug hotkeys

```powershell
python -m vaani --debug
# or: $env:VAANI_DEBUG = "1"; python -m vaani
```

On start, note:

- `[vaani] log file: %LOCALAPPDATA%\Vaani\logs\vaani.log`
- `[vaani] registered Ctrl+Space` (and the other chords)

When you press a chord you should see:

```text
[vaani] hotkey pressed: Ctrl+Space (smart)
event=hotkey_pressed ...
event=recording ...
```

On release:

```text
[vaani] hotkey released: Ctrl+Space (smart)
event=processing ...
```

## Run

Manual transcription smoke test (no paste):

```powershell
python -m vaani --record
```

Full daemon (hotkeys + paste):

```powershell
python -m vaani
```

## Smoke checklist (live Windows machine)

- [ ] `uv pip install -e ".[windows]"` succeeds
- [ ] `python -m vaani --record` prints a transcript after Enter
- [ ] Hold `Ctrl+Space` → pill + waveform → release → processing dots → paste into Notepad
- [ ] New dictation ignored while processing; Esc cancels
- [ ] Changing focus mid-transcription yields clipboard-only (notification / status)
- [ ] Hold `Ctrl+Alt+Space` + “open notepad” launches Notepad
- [ ] “open brave” / “open chrome” opens a browser window
- [ ] Esc cancels an in-flight recording
- [ ] After a crash, restarting Vaani clears any orphan pill

## Notes / blockers for live smoke

- Microphone privacy must allow desktop apps (Settings → Privacy → Microphone).
- Some elevated / UIPI-protected windows ignore synthetic Ctrl+V; text stays on
  the clipboard (`CLIPBOARD_ONLY`).
- Antivirus or accessibility tooling may block `pynput` global hooks — run from a
  normal user session first.
- Stop/cancel from the pill uses the control file (not Unix signals). Hotkeys
  (`Esc`, hold/release chords) still work while the pill is open.
