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

If you see:

```text
This process is not trusted! Input event monitoring will not be possible…
```

then **Cursor alone is not enough**. macOS trusts the **Python binary** that
runs Vaani (often a uv-managed `python3.12` path), not only the editor.

Grant **all three** under System Settings → Privacy & Security:

1. **Microphone** — recording via PortAudio / `sounddevice`
2. **Accessibility** — global hotkeys + ⌘V paste
3. **Input Monitoring** — global hotkeys via `pynput`

Add / enable **both**:

- **Cursor** (or **Terminal** if you start Vaani there)
- The resolved interpreter printed at startup, typically under  
  `~/.local/share/uv/python/.../bin/python3.12`

Then **fully quit Cursor (Cmd+Q)** and reopen — toggling a checkbox without
restarting is not enough.

**Most reliable smoke path:** run Vaani from **Terminal.app** (with Terminal
allowed in Accessibility + Input Monitoring), not from the Cursor terminal:

```bash
cd /path/to/Vaani
source .venv/bin/activate
python -m vaani
```

If Accessibility is denied, Vaani still copies text to the clipboard and returns
`clipboard_only` instead of pasting.

## Hotkeys

macOS uses Carbon hotkeys. At startup you should see `Vaani hotkeys ready:`.

Defaults use **Option+Space** (easier hold-to-talk). Avoids Spotlight (⌘Space);
if another app owns ⌥Space, free that shortcut or tell us and we’ll switch.

| Chord | Keys | Action |
|---|---|---|
| Option+Space | ⌥Space | **Hold** for smart dictation (release = stop) |
| Option+Shift+Space | ⌥⇧Space | **Hold** for literal dictation |
| Control+Option+Space | ⌃⌥Space | **Hold** for assistant |
| Esc | Esc | Cancel in-flight work |

The bottom pill appears while the chord is held. On release it switches to a
**processing** animation (pulsing dots) until paste finishes, then vanishes.
New dictation hotkeys are ignored while processing (Esc still cancels).
The microphone is opened only while recording (`event=mic_open` /
`event=mic_close` in the log) — not for the whole time Vaani is running.

Uploads are compressed (FLAC when `soundfile` is installed; otherwise macOS
`afconvert` m4a) and cleanup uses a fast Groq model for quicker paste.

`--record` working only proves mic + Groq. Global hotkeys need `python -m vaani`
left running.

### Debug hotkeys

```bash
python -m vaani --debug
# or: VAANI_DEBUG=1 python -m vaani
```

On start, note:

- `[vaani] log file: ~/Library/Application Support/Vaani/logs/vaani.log`
- `[vaani] registered Option+Space` (and the other chords)

When you press a chord you should see:

```text
[vaani] hotkey pressed: Option+Space (smart)
event=hotkey_pressed ...
event=recording ...
```

If registration lines appear but **no** `hotkey pressed` line, the OS is not
delivering the shortcut (conflict / wrong modifiers). Tail the log:

```bash
tail -f "$HOME/Library/Application Support/Vaani/logs/vaani.log"
```

## Data paths

| Kind | Location |
|---|---|
| Data / history | `~/Library/Application Support/Vaani` |
| Cache / audio / amplitude | `~/Library/Caches/Vaani` |
| Config | `~/Library/Application Support/Vaani/config` |

While recording, a compact black pill appears at the **bottom** of the screen:
grey **X** (cancel), live waveform, white **✓** (stop & paste). Drag sideways
only — it stays pinned to the bottom. A small **●** may also appear in the menu
bar. Spawn logs: `~/Library/Application Support/Vaani/logs/indicator.err`.

| IPC | Path / env |
|---|---|
| Amplitude | `~/Library/Caches/Vaani/amplitude` (`VAANI_AMPLITUDE_PATH`) |
| Stop/cancel control | `~/Library/Caches/Vaani/indicator_control.json` (`VAANI_INDICATOR_CONTROL`) |
| Pill position | `~/Library/Application Support/Vaani/indicator.json` |

If a pill is stuck after a crash: quit Vaani, or
`pkill -f 'vaani.platform.macos.indicator_app'`.

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
   - Hold Option+Space, speak, release to stop
   - Text should paste into the focused field when Accessibility is granted

3. **Assistant app open**

   - Hold Control+Option+Space, say “open Terminal”
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
