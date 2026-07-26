# Vaani on macOS

Install and permission checklist for the Phase 1 macOS adapter (P1-02).

## Requirements

- macOS 12+ (Ventura/Sonoma/Sequoia recommended)
- Python 3.10+
- Microphone hardware
- Accessibility + Input Monitoring grants for paste and global hotkeys

## Setup

```bash
cd /path/to/Vaani
python3 -m venv .venv
source .venv/bin/activate
uv pip install -e '.[macos]'
# or: pip install -e '.[macos]'
```

Optional extras pull in `sounddevice`, `pyperclip`, and `pynput`. Core deps
(`httpx`, `keyring`) install with the package; `python-xlib` is Linux-only and
is not required on Darwin.

Store a Groq key in the macOS Keychain (via `keyring`) or export:

```bash
export GROQ_API_KEY='…'
```

## Permissions (required for live use)

Grant these to the terminal or app that launches `python -m vaani`
(System Settings → Privacy & Security):

1. **Microphone** — recording via PortAudio / `sounddevice`
2. **Accessibility** — synthetic ⌘V paste and some focus probes
3. **Input Monitoring** — global hotkeys via `pynput`

If Accessibility is denied, Vaani still copies text to the clipboard and returns
`clipboard_only` instead of pasting. You will see a notification when paste is
unavailable or the focused app changed.

## Hotkeys

These use the **Control** key (⌃), not **Command** (⌘).
Command+Space stays with Spotlight.

| Chord | Keys | Action |
|---|---|---|
| Control+Space | ⌃Space | Smart dictation toggle |
| Control+Shift+Space | ⌃⇧Space | Literal dictation toggle |
| Control+Alt+Space | ⌃⌥Space | Assistant toggle |
| Esc | Esc | Cancel in-flight work |

Paste into apps still uses normal macOS **⌘V** under the hood — that is
separate from the recording hotkey.

If Control+Space does nothing, grant **Input Monitoring** to Terminal/Cursor
and restart Vaani. Then try Control+Shift+Space.

## Data paths

| Kind | Location |
|---|---|
| Data / history | `~/Library/Application Support/Vaani` |
| Cache / audio / amplitude | `~/Library/Caches/Vaani` |
| Config | `~/Library/Application Support/Vaani/config` |

Amplitude IPC for a future indicator lives at
`~/Library/Caches/Vaani/amplitude` (`VAANI_AMPLITUDE_PATH`).

## Smoke checklist

Run from the repo with the venv active:

1. **Record-only transcription**

   ```bash
   python -m vaani --record
   ```

   Speak a short phrase; expect a transcript on stdout (no paste).

2. **Smart dictation paste**

   - Open TextEdit (or a browser text field)
   - Start Vaani: `python -m vaani`
   - Press Ctrl+Space, speak, press Ctrl+Space again
   - Text should paste into the focused field when Accessibility is granted

3. **Assistant app open**

   - Press Ctrl+Alt+Space
   - Say “Open Terminal”
   - Terminal.app should launch (`open -a Terminal`)

4. **Focus-change → clipboard-only**

   - Start dictation, then switch to another app before stop
   - Expect notification that text was copied only (no paste)

5. **Browser open** (assistant)

   - Say “Open example.com in Chrome” / Brave-oriented phrasing per assistant
     site intents
   - Prefer Brave, then Chrome, then the default `open URL` handler

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| Hotkeys do nothing | Input Monitoring not granted; restart the process after granting |
| Clipboard updates but no paste | Accessibility denied → expected `clipboard_only` |
| `sounddevice` import / open errors | Reinstall `.[macos]`; check Microphone permission |
| Keyring errors | Use `GROQ_API_KEY` env override |

## Manual acceptance (PR)

- [ ] `--record` prints transcript
- [ ] Smart dictation pastes into TextEdit or browser
- [ ] “Open Terminal” via assistant mode
- [ ] Focus-change ⇒ clipboard-only
