# Vaani on Windows

Windows MVP support for microphone capture, global hotkeys, clipboard paste,
assistant app/browser launch, and notifications.

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

## Hotkeys

| Chord | Action |
|---|---|
| `Ctrl+Space` | Smart dictation toggle |
| `Ctrl+Shift+Space` | Literal dictation toggle |
| `Ctrl+Alt+Space` | Assistant mode |
| `Esc` | Cancel in-flight recording / request |

If a chord is already claimed by another app, Vaani cannot steal it — free the
shortcut or change the other app’s binding.

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
- [ ] `Ctrl+Space` starts/stops smart dictation and pastes into Notepad
- [ ] Changing focus mid-transcription yields clipboard-only (notification / status)
- [ ] `Ctrl+Alt+Space` + “open notepad” launches Notepad
- [ ] “open brave” / “open chrome” opens a browser window
- [ ] Esc cancels an in-flight recording

## Notes / blockers for live smoke

- Microphone privacy must allow desktop apps (Settings → Privacy → Microphone).
- Some elevated / UIPI-protected windows ignore synthetic Ctrl+V; text stays on
  the clipboard (`CLIPBOARD_ONLY`).
- Antivirus or accessibility tooling may block `pynput` global hooks — run from a
  normal user session first.
- There is no Linux-style `SIGUSR` control channel on Windows MVP; use hotkeys
  (or stop the process) to control the daemon.
