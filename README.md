# Vaani

Vaani is a personal, cloud-assisted voice dictation and command tool for
Ubuntu. Press a global shortcut, speak, press the shortcut again, and Vaani
transcribes the recording with Groq. It can paste cleaned text into the focused
application, preserve literal speech, answer an explicit question, open common
applications or sites, or pass a spoken task to Codex CLI.

This repository is the recoverable, sanitized copy of the working Linux
application. Private URLs, credentials, browser history, transcript history,
logs, and machine-specific paths are intentionally not stored in Git.

> [!IMPORTANT]
> Vaani currently targets **Ubuntu 22.04, GNOME, Xorg/X11, and x86_64**. It is
> not yet a Windows, macOS, or Wayland application. Planned cross-platform
> operator and remote work lives in [ROADMAP.md](ROADMAP.md).

## Contents

- [Roadmap](ROADMAP.md)
- [What works](#what-works)
- [How Vaani works](#how-vaani-works)
- [Requirements](#requirements)
- [Fresh installation](#fresh-installation)
- [First run](#first-run)
- [Shortcuts and controls](#shortcuts-and-controls)
- [Operating modes](#operating-modes)
- [Configuration](#configuration)
- [Data and privacy](#data-and-privacy)
- [Safe operation](#safe-operation)
- [Troubleshooting](#troubleshooting)
- [Development](#development)
- [Architecture](#architecture)
- [Backup and recovery](#backup-and-recovery)
- [Current limitations](#current-limitations)

## What works

- Toggle-to-record dictation from any X11 application.
- English, Hindi, and Hinglish transcription through Groq
  `whisper-large-v3-turbo`.
- Smart cleanup that removes filler and returns English or Latin-script
  Hinglish.
- Literal dictation that bypasses cleanup.
- Automatic paste with `Ctrl+V` in editors and browsers.
- Automatic `Ctrl+Shift+V` paste in recognized terminal emulators.
- Clipboard-only fallback when the focused target changes or paste injection
  cannot be verified.
- A GTK4 recording pill with live microphone amplitude and cancel/stop
  controls; drag requests remain compositor-dependent.
- Local SQLite transcript history.
- Deterministic application and browser launching before an assistant request
  reaches Codex.
- Optional Codex CLI assistant requests.
- Private site aliases stored outside Git.
- GNOME login autostart.
- Sanitized rotating logs and temporary-audio cleanup.

## How Vaani works

```text
global X11 shortcut
        |
        v
PulseAudio capture (parec, 16 kHz mono WAV)
        |
        v
Groq transcription (Whisper)
        |
        +--> literal mode --------> clipboard + paste
        |
        +--> smart cleanup -------> clipboard + paste
        |
        +--> explicit answer -----> Groq answer -> clipboard + paste
        |
        +--> assistant mode ------> local app/site action or Codex CLI
        |
        v
private SQLite history + sanitized operational log
```

Vaani captures the focused X11 window immediately before delivery and verifies
that it remains unchanged while paste is dispatched. It does not preserve the
target from recording start, so keep the intended destination focused when
processing finishes.

## Requirements

### Supported environment

- Ubuntu 22.04 LTS
- GNOME desktop session
- Xorg/X11 session (`XDG_SESSION_TYPE=x11`)
- x86_64 CPU
- Python 3.10 or newer
- Network access to the Groq API
- A Groq API key

### Required operating-system components

- PulseAudio-compatible capture through `parec` and `pactl`
- X11 input inspection through `xinput`
- X11 clipboard fallback through `xclip`
- XTEST support in the X server
- GTK4 and PyGObject for the recording widget
- GNOME Keyring/Secret Service for the Groq key
- `notify-send` for notifications

### Optional assistant dependency

Codex assistant mode requires an installed and authenticated Codex CLI.
Dictation does not require Codex.

## Fresh installation

These commands assume a fresh Ubuntu 22.04 GNOME/Xorg machine and a clone path
without spaces.

### 1. Install system packages

```bash
sudo apt update
sudo apt install -y \
  curl \
  git \
  gh \
  python3.10 \
  python3.10-venv \
  python3-gi \
  gir1.2-gtk-4.0 \
  libgtk-4-1 \
  pulseaudio-utils \
  xclip \
  xinput \
  libnotify-bin \
  gnome-keyring
```

Log out and back in after installing `gnome-keyring` if the Secret Service is
not available in the current desktop session.

### 2. Clone the private repository

Authenticate GitHub CLI through its browser flow, then clone:

```bash
gh auth login --hostname github.com --git-protocol https --web
gh auth status
gh repo clone 0-Gaurav-0/Vaani
cd Vaani
```

Do not place a GitHub token in the clone URL.

### 3. Install `uv`

If `uv` is not already installed:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
uv --version
```

### 4. Create the Python environment

PyGObject is supplied by Ubuntu, so the virtual environment must be allowed to
see system site packages:

```bash
uv venv --python python3.10 --system-site-packages .venv
uv pip sync --python .venv/bin/python requirements.lock
uv pip install --python .venv/bin/python --no-deps --editable .
```

Verify Python, GTK4, and the X11 library:

```bash
.venv/bin/python -c 'import gi; gi.require_version("Gtk", "4.0"); from gi.repository import Gtk; print("GTK4 OK")'
.venv/bin/python -c 'from Xlib.display import Display; d=Display(); print("X11/XTEST OK:", d.has_extension("XTEST")); d.close()'
```

### 5. Verify the audio source

```bash
pactl get-default-source
.venv/bin/python -c 'from vaani.audio import preflight; preflight(); print("Microphone preflight OK")'
```

The preflight checks the recorder and default source without retaining a
recording.

### 6. Store the Groq key safely

The recommended location is GNOME Keyring. This command prompts without
displaying or placing the key in shell history:

```bash
.venv/bin/python -c 'from getpass import getpass; from vaani.secrets import SecretServiceKeyStore; SecretServiceKeyStore().set(getpass("Groq API key: ")); print("Groq key stored in the system keyring")'
```

Confirm that a key can be read without printing it:

```bash
.venv/bin/python -c 'from vaani.secrets import SecretServiceKeyStore; print("Groq key configured" if SecretServiceKeyStore().get() else "Groq key missing")'
```

Validate the key and required Groq models:

```bash
.venv/bin/python - <<'PY'
from vaani.secrets import SecretServiceKeyStore, validate_key
from vaani.types import GroqModelSettings

key = SecretServiceKeyStore().get()
result = validate_key(key or "", GroqModelSettings())
print(result)
PY
```

Never commit a key, put it in `sites.json`, paste it into an issue, or include it
in a launcher command. `GROQ_API_KEY` is supported as a process-local override,
but the keyring is safer for normal use.

### 7. Install launchers and GNOME autostart

The Python package does not install the shell launchers or desktop entry. Copy
them explicitly:

```bash
mkdir -p "$HOME/.local/bin" "$HOME/.config/autostart"

install -m 755 scripts/vaani-start "$HOME/.local/bin/vaani-start"
install -m 755 scripts/vaani-toggle "$HOME/.local/bin/vaani-toggle"
install -m 755 scripts/vaani-assistant "$HOME/.local/bin/vaani-assistant"
install -m 755 scripts/vaani-cancel "$HOME/.local/bin/vaani-cancel"

install -m 644 data/vaani-autostart.desktop \
  "$HOME/.config/autostart/vaani.desktop"

repo_dir="$(pwd -P)"
sed -i \
  "s|^Exec=.*|Exec=env VAANI_PROJECT_DIR=$repo_dir $HOME/.local/bin/vaani-start|" \
  "$HOME/.config/autostart/vaani.desktop"
```

Inspect the installed command before enabling it:

```bash
grep '^Exec=' "$HOME/.config/autostart/vaani.desktop"
```

The resulting desktop file contains a local absolute path by design. It lives in
`~/.config/autostart`, not in Git. Regenerate it after moving the repository.

## First run

### Confirm the desktop session

```bash
printf 'Session type: %s\n' "$XDG_SESSION_TYPE"
printf 'Display: %s\n' "$DISPLAY"
```

Both must describe an active X11 desktop. A Wayland session is unsupported.

### Test transcription without global hotkeys

```bash
.venv/bin/python -m vaani --record
```

Speak, press Enter, and confirm that text appears in the terminal. This path
tests microphone capture, the keyring, Groq authentication, upload, and
transcription. It does not paste automatically.

### Run Vaani in the foreground

```bash
.venv/bin/python -m vaani
```

Keep this terminal open during the first shortcut test. Press `Ctrl+C` only when
Vaani is idle.

### Start it in the background

After the foreground test succeeds:

```bash
setsid -f env VAANI_PROJECT_DIR="$PWD" "$HOME/.local/bin/vaani-start"
pgrep -af '^\.venv/bin/python -m vaani$'
```

The autostart entry runs the same launcher on the next GNOME login.

## Shortcuts and controls

Press a mode shortcut once to begin recording and the same shortcut again to
stop and process it.

| Control | Behavior | Implementation |
|---|---|---|
| `Ctrl+Space` | Smart dictation | X11 passive grab |
| `Ctrl+Shift+Space` | Literal dictation | X11 passive grab |
| `Ctrl+Alt+Space` | Assistant mode | X11 passive grab |
| `Ctrl+Super+Space` | Alternate assistant trigger | `xinput` polling |
| `Esc` | Cancel active recording or processing | `xinput` polling |
| Widget X button | Cancel without delivering text | Signal to Vaani |
| Widget red stop button | Stop recording and process | Signal to Vaani |
| Drag the widget center | Ask the X11 compositor to move it | GTK4 gesture |

Launcher equivalents:

```bash
vaani-toggle
vaani-assistant
vaani-cancel
```

The launcher scripts match a process whose command line is exactly
`.venv/bin/python -m vaani`. Start Vaani through `vaani-start` or that exact
Python command if you rely on the launchers.

### Shortcut compatibility warning

`Ctrl+Super+Space` and global `Esc` use an `xinput` polling path with these
current assumptions:

- Control keycode `37`
- Super keycode `133`
- Space keycode `65`
- Escape keycode `9`
- Keyboard name `AT Translated Set 2 keyboard`
- Fallback device ID `11`

Those values are common on the reference laptop, not universal. `Ctrl+Space`,
`Ctrl+Shift+Space`, and `Ctrl+Alt+Space` use X11 key symbols and are more
portable across X11 keyboards.

## Operating modes

### Smart dictation

Use `Ctrl+Space`.

1. Vaani records a 16 kHz mono WAV.
2. Groq Whisper transcribes without forcing a language.
3. The cleanup model removes filler and preserves corrections and meaning.
4. Hindi/Devanagari cleanup is transliterated to Latin-script Hinglish.
5. The result is copied and pasted into the unchanged focused window.

If cleanup fails or its response is unsafe or malformed, Vaani falls back to
the raw transcript.

### Literal dictation

Use `Ctrl+Shift+Space`.

Literal mode skips smart cleanup and delivers the raw trimmed transcription.
Use it for code, punctuation-sensitive speech, names, or wording that should
not be rewritten.

### Answer-prefix behavior

Answer generation currently works reliably only through **literal dictation**.
Use `Ctrl+Shift+Space` and start with one of:

- “Answer this …”
- “Only answer …”
- “Question …”

Vaani sends the remaining question to the Groq chat model and pastes only the
answer.

Known bug: smart mode currently overwrites the generated answer with cleanup of
the original transcript, and assistant mode routes the original transcript
instead. Do not rely on answer prefixes in those two modes until the controller
flow is fixed.

### Assistant mode

Use `Ctrl+Alt+Space` or, on the reference keyboard, `Ctrl+Super+Space`.

Assistant transcription currently forces English. After transcription, Vaani
tries these routes in order:

1. Resolve and launch a known desktop application.
2. Resolve a configured site or direct browser request.
3. Send the spoken request to Codex CLI.

Brave is preferred for generic browser/site requests unless Chrome is named
explicitly. Site URLs are opened in a new window. App and site actions return a
desktop notification and are written to local history.

#### Codex boundary

Codex requests are intentionally isolated:

- Each request starts a new `codex exec --ephemeral` session.
- `--ignore-user-config` is enabled.
- User-configured MCP servers, skills, and related Codex configuration are
  therefore **not loaded**.
- Reasoning effort is set to `low`.
- The default timeout is 30 seconds.
- The working directory is `VAANI_ASSISTANT_CWD`, or the home directory when
  unset.
- Codex CLI must already be installed and authenticated.
- The process runs with the desktop user's operating-system permissions.
- The desktop environment is inherited except for `OPENAI_API_KEY` and
  `GROQ_API_KEY`; other environment variables are not stripped.
- The spoken prompt is passed as a Codex command argument and may be transiently
  visible to local process inspection.

This mode does not reuse an interactive Codex session and does not dynamically
load MCPs. Treat spoken assistant tasks as commands executed with your user
permissions.

## Configuration

### Assistant workspace

Set the directory where Codex assistant requests run:

```bash
export VAANI_ASSISTANT_CWD="$HOME/path/to/workspace"
```

For autostart, add the variable to the installed desktop entry:

```text
Exec=env VAANI_PROJECT_DIR=/path/to/Vaani VAANI_ASSISTANT_CWD=/path/to/workspace /home/user/.local/bin/vaani-start
```

Use real local paths only in the installed file under `~/.config/autostart`.
Do not commit them.

### Private site shortcuts

Private URLs belong in:

```text
~/.config/vaani/sites.json
```

Start from the safe example:

```bash
mkdir -p "$HOME/.config/vaani"
install -m 600 config/sites.example.json "$HOME/.config/vaani/sites.json"
```

Schema:

```json
[
  {
    "aliases": ["project dashboard", "dashboard"],
    "name": "Project Dashboard",
    "url": "https://example.com/dashboard"
  }
]
```

Rules:

- `aliases` must be a non-empty list of phrases.
- `name` is the notification label.
- `url` must use `http` or `https`.
- Invalid entries are ignored.
- Local entries are checked before the built-in public catalog.
- Keep this file private and never add it to Git.

Example request: “Open the project dashboard in Brave.”

### Built-in public sites

The sanitized repository contains generic aliases for Gmail, Basecamp, Paddle,
Stripe, Postmark, Agent Skills, ChatGPT, Claude, and Gemini. Account-specific
destinations must stay in the private local configuration.

### Groq models and limits

Current defaults:

| Purpose | Model or limit |
|---|---|
| Transcription | `whisper-large-v3-turbo` |
| Cleanup and answers | `openai/gpt-oss-120b` |
| Recording auto-stop | 300 seconds |
| WAV validation maximum | 600 seconds |
| Minimum recording | 250 milliseconds |
| Maximum audio file | 25 MiB |
| Transcription deadline | 150 seconds |
| Cleanup/answer deadline | 75 seconds |

The effective live recording limit is five minutes because the controller
auto-stops before the WAV validator's ten-minute ceiling.

## Data and privacy

Vaani is local-first for orchestration, not local-only for processing.

### Cloud boundary

- Recorded audio is uploaded to Groq for transcription.
- Smart cleanup sends transcript text to a Groq chat model.
- Answer-prefix behavior sends the question to a Groq chat model.
- Codex assistant tasks are sent wherever the authenticated Codex CLI sends
  them.

### Local data

| Path | Contents | Retention |
|---|---|---|
| `~/.local/share/vaani/history.sqlite3` | Plaintext raw and final transcripts plus metadata | Unlimited by default |
| `~/.local/share/vaani/logs/vaani.log` | Sanitized state/error events | 1 MiB plus three rotations |
| `~/.local/share/vaani/logs/autostart.log` | Launcher stdout/stderr | Not rotated by Vaani |
| `~/.cache/vaani/audio/` | Temporary WAV files | Deleted after transcription or swept at startup |
| `~/.config/vaani/sites.json` | Optional private site aliases | Until manually changed |
| `/tmp/vaani-amplitude` | Latest scalar RMS amplitude only | Temporary |
| System keyring | Groq API key | Until removed |

History and runtime directories are created with private permissions. The
history database stores transcripts in plaintext and currently has no
configured retention limit.

After delivery, the final text remains on the desktop clipboard. Clipboard
managers may retain it independently of Vaani.

Audio is normally deleted in a `finally` block after transcription. A hard
process or machine crash can leave a temporary WAV until Vaani next starts and
sweeps the audio directory.

Logs redact recognized keys and transcript-like payload fields, but they should
still be treated as private operational data.

## Safe operation

### Stop versus cancel

- **Stop** ends recording, enters processing, transcribes, and delivers.
- **Cancel** invalidates the operation, removes temporary audio, and does not
  intentionally deliver partial text.

### Safe restart rule

Never restart, update, or kill Vaani during recording or processing. Wait until
the log reports one of:

```text
event=delivered
event=assistant_complete
event=cancelled
event=failure
```

Watch the log:

```bash
tail -f "$HOME/.local/share/vaani/logs/vaani.log"
```

After Vaani is idle, stop it gracefully:

```bash
pkill -TERM -f '^\.venv/bin/python -m vaani$'
```

Start it again:

```bash
setsid -f env VAANI_PROJECT_DIR="$PWD" "$HOME/.local/bin/vaani-start"
```

## Troubleshooting

### Vaani does not start

Check the session and display:

```bash
printf 'XDG_SESSION_TYPE=%s\nDISPLAY=%s\n' "$XDG_SESSION_TYPE" "$DISPLAY"
.venv/bin/python -c 'from Xlib.display import Display; d=Display(); print(d.get_display_name()); d.close()'
```

Check for a running process and inspect logs:

```bash
pgrep -af '^\.venv/bin/python -m vaani$'
tail -n 100 "$HOME/.local/share/vaani/logs/vaani.log"
tail -n 100 "$HOME/.local/share/vaani/logs/autostart.log"
```

Run in the foreground for the clearest startup result:

```bash
.venv/bin/python -m vaani
```

### A shortcut does nothing

Confirm that only one Vaani process is running:

```bash
pgrep -af '^\.venv/bin/python -m vaani$'
```

Inspect X11 devices and actual key events:

```bash
xinput list
xinput test-xi2 3
```

Press the problem shortcut and look for key press/release events. Exit the test
with `Ctrl+C`.

If `Ctrl+Space`, `Ctrl+Shift+Space`, or `Ctrl+Alt+Space` fails, another
application or GNOME shortcut may already own the passive grab. Remove that
conflict and restart Vaani only after it is idle.

If only `Ctrl+Super+Space` or global `Esc` fails, compare your device and
keycodes with the assumptions in
[`src/vaani/hotkeys.py`](src/vaani/hotkeys.py).

### The recording widget is missing

Verify GTK4 inside the Vaani environment:

```bash
.venv/bin/python -c 'import gi; gi.require_version("Gtk", "4.0"); from gi.repository import Gtk; print("GTK4 OK")'
```

Check the indicator process:

```bash
pgrep -af 'python.*-m vaani\.indicator'
```

The indicator intentionally falls back to a headless process when GTK cannot
initialize, so dictation may still work while the widget is invisible.

### The waveform does not react

While recording, inspect the scalar amplitude:

```bash
watch -n 0.2 cat /tmp/vaani-amplitude
```

The value should rise above zero while speaking. If it does not, verify the
default source:

```bash
pactl get-default-source
pactl list short sources
```

### Transcription is empty, repetitive, or says the wrong thing

Test the exact audio/Groq path:

```bash
.venv/bin/python -m vaani --record
```

Then check:

```bash
pactl get-default-source
.venv/bin/python -c 'from vaani.audio import preflight; preflight(); print("Audio OK")'
```

Select the intended microphone in GNOME Settings if the default source is a
monitor, disconnected device, or low-quality input.

### “API key required” or keyring errors

Confirm Secret Service and the masked key state:

```bash
busctl --user status org.freedesktop.secrets
.venv/bin/python -c 'from vaani.secrets import SecretServiceKeyStore; print("configured" if SecretServiceKeyStore().get() else "missing")'
```

If the keyring is locked, unlock the GNOME login keyring and retry. To replace
the key, run the hidden prompt from the installation section.

To remove the stored key:

```bash
.venv/bin/python -c 'from vaani.secrets import SecretServiceKeyStore; SecretServiceKeyStore().remove(); print("Groq key removed")'
```

### Groq errors or timeouts

Validate model access:

```bash
.venv/bin/python - <<'PY'
from vaani.secrets import SecretServiceKeyStore, validate_key
from vaani.types import GroqModelSettings

print(validate_key(SecretServiceKeyStore().get() or "", GroqModelSettings()))
PY
```

Check the sanitized category in `vaani.log`. Common categories include network,
quota, timeout, malformed response, microphone, and paste failure.

### Text reaches the clipboard but does not paste

Inspect the clipboard:

```bash
xclip -selection clipboard -o
```

Vaani deliberately avoids pasting if focus changed during processing. Return to
the intended field and paste manually.

Recognized terminal classes receive `Ctrl+Shift+V`; editors and browsers receive
`Ctrl+V`. An unknown terminal class may therefore need manual paste.

### Assistant requests time out or ignore MCPs

Verify Codex:

```bash
codex --version
codex login status
```

The 30-second assistant timeout is intentional. The current runner also uses
`--ignore-user-config`, so user MCPs and skills are not loaded. This is current
behavior, not an authentication failure.

### A private site does not open

Validate the JSON:

```bash
.venv/bin/python -m json.tool "$HOME/.config/vaani/sites.json"
```

Only `http` and `https` URLs are accepted. Confirm the spoken command contains
an action word such as “open,” “launch,” “go to,” or “show,” followed by a
configured alias.

## Development

### Recreate the development environment

```bash
uv venv --python python3.10 --system-site-packages .venv
uv pip sync --python .venv/bin/python requirements.lock
uv pip install --python .venv/bin/python --no-deps --editable .
```

### Run the complete suite

```bash
PYTHONDONTWRITEBYTECODE=1 \
  .venv/bin/pytest -q -p no:cacheprovider
```

The keyring probe is opt-in:

```bash
VAANI_KEYRING_PROBE=1 \
  .venv/bin/pytest -q tests/integration/test_keyring_probe.py -rs
```

Run the X11 integration check inside the active Xorg session:

```bash
DISPLAY="$DISPLAY" \
  .venv/bin/pytest -q tests/integration/test_x11_live.py -rs
```

A skip means the environmental integration was not proven; it is not a pass.

### Run focused tests

```bash
.venv/bin/pytest -q tests/unit/test_controller.py
.venv/bin/pytest -q tests/unit/test_hotkeys.py
.venv/bin/pytest -q tests/unit/test_delivery.py
.venv/bin/pytest -q tests/unit/test_sites.py
```

### Scan tracked files for secrets

```bash
git ls-files -z |
  xargs -0 uvx --from detect-secrets detect-secrets scan
```

Review every finding. References to the `secrets.py` module and synthetic test
values can be false positives; an unexplained high-entropy value blocks a push.

Also inspect history for common credential prefixes without printing matches
into a report:

```bash
git log --all --oneline -G'gsk_|gh[pousr]_|github_pat_|AKIA|BEGIN .*PRIVATE KEY'
```

### Update dependencies

Edit `requirements.in`, then regenerate the platform lock:

```bash
uv pip compile requirements.in \
  --python-version 3.10 \
  --python-platform x86_64-manylinux_2_35 \
  --generate-hashes \
  --output-file requirements.lock
```

Run the complete suite and security scans before committing a lock update.

## Architecture

### Source map

| Module | Responsibility |
|---|---|
| `__main__.py` | Runtime assembly, X11 loop, signals, and manual record command |
| `controller.py` | Recording state machine, cancellation, mode routing, and history writes |
| `audio.py` | `parec` lifecycle, WAV validation, and temporary-file cleanup |
| `groq.py` | Transcription, cleanup, answers, retries, limits, and redaction-safe errors |
| `delivery.py` | Clipboard ownership, target verification, and XTEST paste |
| `hotkeys.py` | X11 passive grabs plus alternate `xinput` polling |
| `indicator.py` | GTK4 recording pill and scalar-amplitude rendering |
| `x11.py` | Active-window and input-focus snapshots |
| `history.py` | Private SQLite schema and concurrency-safe transcript storage |
| `secrets.py` | GNOME Keyring access and Groq model validation |
| `codex.py` | Bounded, cancellable, ephemeral Codex CLI execution |
| `apps.py` | Deterministic desktop application resolution and launch |
| `sites.py` | Public aliases plus optional private local site configuration |
| `feedback.py` | Recording sounds, notifications, and indicator lifecycle |
| `observability.py` | Sanitized rotating logs and error categories |
| `config.py` | Runtime paths, permissions, timeouts, and audio limits |
| `types.py` | Shared states, settings, results, and protocols |

### State model

```text
Idle
  |
  | shortcut
  v
Recording
  |        \
  | stop    \ cancel
  v          v
Processing  Idle
  |
  | delivered / displayed / failure
  v
Idle
```

Each operation receives a token. Cancellation invalidates the token so a late
worker result cannot be inserted into history or delivered after the operation
has been cancelled.

### Source of truth

Historical documents under `docs/superpowers/` record earlier designs and
plans. When they disagree with runtime source or executable tests, current
source and tests are authoritative.

## Backup and recovery

### What to back up

- The Git repository or its private GitHub remote
- `~/.config/vaani/sites.json`, if used
- `~/.local/share/vaani/history.sqlite3`, if transcript history matters
- `~/.local/share/vaani/logs/`, only when operational history is useful
- The installed autostart desktop file as a reference, or regenerate it

Do not back up:

- A plaintext Groq key
- `~/.cache/vaani/audio/`
- `/tmp/vaani-amplitude`
- `.venv/`
- Python caches or test caches

### Create a local data backup

Wait until Vaani is idle and stop it gracefully. Then:

```bash
backup_dir="$HOME/vaani-backup-$(date +%Y%m%d-%H%M%S)"
mkdir -m 700 "$backup_dir"

if [ -f "$HOME/.config/vaani/sites.json" ]; then
  install -m 600 "$HOME/.config/vaani/sites.json" "$backup_dir/sites.json"
fi

if [ -f "$HOME/.local/share/vaani/history.sqlite3" ]; then
  install -m 600 \
    "$HOME/.local/share/vaani/history.sqlite3" \
    "$backup_dir/history.sqlite3"
fi
```

Re-enter the Groq key into the destination machine's keyring instead of
exporting it to plaintext.

### Restore

1. Clone the private repository.
2. Repeat the fresh installation.
3. Restore `sites.json` with mode `0600`.
4. Restore `history.sqlite3` with mode `0600` while Vaani is stopped.
5. Store the Groq key through the hidden keyring prompt.
6. Regenerate the autostart entry for the new repository path.
7. Run `--record`, then foreground shortcut testing, before enabling autostart.

Example restore:

```bash
install -Dm600 /path/to/backup/sites.json \
  "$HOME/.config/vaani/sites.json"
install -Dm600 /path/to/backup/history.sqlite3 \
  "$HOME/.local/share/vaani/history.sqlite3"
```

Skip a command when that backup file does not exist.

## Current limitations

See [ROADMAP.md](ROADMAP.md) for planned work beyond this list.

- Linux/X11 only; no Windows, macOS, or Wayland backend.
- Ubuntu 22.04 GNOME/Xorg x86_64 is the validated platform.
- Cloud transcription requires network access, a Groq key, and available quota.
- Recordings are not transcribed incrementally or streamed as text.
- Live recording auto-stops after five minutes.
- History is plaintext and unlimited by default; there is no history UI.
- Delivered text remains on the system clipboard.
- Alternate assistant and global Escape polling assumes reference-laptop
  keycodes and a particular keyboard device.
- Widget movement is compositor-dependent and its position is not currently
  restored automatically by the runtime.
- Answer prefixes work reliably only in literal mode.
- Assistant transcription currently forces English.
- Codex assistant requests use a 30-second timeout and ignore user
  configuration, MCPs, and skills.
- There is no assistant chat UI or persistent Codex conversation.
- Launcher signals require Vaani's expected process command line.
- Launcher and autostart files must be installed separately from the Python
  package.
- Private sites require local configuration and are intentionally absent from
  Git.

## Security checklist before pushing changes

- [ ] No `.env`, key, token, cookie, credential file, or private URL is tracked.
- [ ] No transcript database, audio, log, cache, backup, or virtual environment
      is tracked.
- [ ] Tests pass from the documented environment.
- [ ] Keyring and X11 integration checks were run or their skips are disclosed.
- [ ] `detect-secrets` findings were reviewed.
- [ ] Git history contains no credential prefix or corporate/private metadata.
- [ ] The GitHub repository remains private.

For the operational restart rule, also see
[`docs/OPERATIONS.md`](docs/OPERATIONS.md).
