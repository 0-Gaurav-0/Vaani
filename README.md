# Vaani

Vaani is a personal voice dictation and command tool. Hold a shortcut, speak,
and release it to paste text or run a voice command. It supports English,
Hindi, and mixed Hinglish speech through cloud transcription.

The main desktop target is **Ubuntu 22.04, GNOME, and Xorg/X11**. Linux
Wayland, macOS, and Windows adapters also exist. Their setup and feature
limits are in the [installation guides](docs/install/README.md).
Meeting mode currently requires the Linux/X11 runtime.

## Current features

- **Hold-to-talk dictation:** hold the ThinkPad TrackPoint middle button to
  record; release it to transcribe and paste.
- **Hands-free dictation:** use the Fn latch and middle button to record
  without holding the button. Dictation stops and processes at ten minutes.
- **Hinglish text:** Groq Whisper supports mixed English and Hindi. Optional
  Gemini dictation returns Latin-script Hinglish without translation.
- **Voice commands:** open apps, sites, folders, and projects; play music;
  control media and volume; or open an app and type text into it.
- **Assistant:** optional Jev routing through OpenRouter, with Hermes CLI or
  Codex CLI for delegated tasks. Action previews offer a cancel window.
- **Recording pill:** a draggable glass pill with waveform, timer, stop, and
  cancel controls. It remembers its position per monitor.
- **Recording recovery:** dictation chunks and text remain on disk for up to
  48 hours between archive sweeps. Failed and crashed recordings are preserved
  when audio is still available.
- **Meeting mode:** record the microphone and system output, pause or resume,
  then create a Markdown transcript with speaker labels and timestamps.
- **Pinned local releases:** install a tested Git snapshot separately from
  the work folder, with a command to restore the previous release.

The latest changes also fix PipeWire capture tail loss, reduce audio startup
work, and avoid stopping a recorder owned by another live Vaani process.
See [CHANGELOG.md](CHANGELOG.md) for the October 2026 update and
[ROADMAP.md](ROADMAP.md) for planned work.

## How it works

```text
Dictation shortcut → microphone capture → Groq or optional Gemini
                                      → text → clipboard + paste

Assistant shortcut → transcription → app/site/media action
                                   → optional OpenRouter routing
                                   → optional Hermes/Codex task

Meeting shortcut → microphone + system capture → saved meeting job
                                              → background Gemini worker
                                              → transcript.md
```

On X11, Vaani checks the target window before it pastes. If delivery fails or
focus changes during dispatch, use the clipboard to paste manually.

## Installation

Use the [OS installation matrix](docs/install/README.md) for all platforms.
The following commands prepare the Linux/X11 development environment.

### 1. Install system packages

```bash
sudo apt update
sudo apt install -y git curl python3.10 python3.10-venv python3-gi \
  gir1.2-gtk-4.0 libgtk-4-1 pulseaudio-utils xclip xinput \
  libnotify-bin gnome-keyring ffmpeg
```

Meeting mode also uses `systemd-run --user` and `xdg-user-dir`.
The audio tools must have access to the desktop user's PulseAudio or PipeWire
session. Meeting preparation uses `ffmpeg` and `ffprobe`.

### 2. Clone and install

```bash
git clone https://github.com/0-Gaurav-0/Vaani.git
cd Vaani
```

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) if needed.
Then create the environment:

```bash
uv venv --python python3.10 --system-site-packages .venv
uv pip sync --python .venv/bin/python requirements.lock
uv pip install --python .venv/bin/python --no-deps --editable .
```

GTK uses the system PyGObject package. The dependency lock includes the audio
libraries used for FLAC compression.

### 3. Configure credentials

Store the Groq key in the system keyring with a hidden prompt:

```bash
.venv/bin/python -c 'from getpass import getpass; from vaani.secrets import SecretServiceKeyStore; SecretServiceKeyStore().set(getpass("Groq API key: "))'
```

Optional providers use environment variables. Copy `.env.example` to `.env`
and edit it locally. Vaani loads `.env` at startup; the file is ignored by Git.
Do not put credentials in a command, issue, commit, or screenshot.

| Variable | Purpose |
|---|---|
| `GROQ_API_KEY` | Process-local override for the Groq keyring value |
| `GEMINI_API_KEY` | Gemini dictation and meeting transcription |
| `VAANI_USE_GEMINI=1` | Enable Gemini dictation; a key alone does not enable it |
| `VAANI_GEMINI_MODEL` | Dictation model override; default `gemini-2.5-flash` |
| `OPENROUTER_API_KEY` | Optional Jev assistant routing and answers |
| `VAANI_JEV=0` | Disable Jev |
| `VAANI_AGENT_BIN` | Choose the agent CLI; default Hermes if available, then Codex |
| `VAANI_ASSISTANT_CWD` | Agent task directory |
| `VAANI_COMPUTER_USE=1` | Enable experimental Linux computer use with `NVIDIA_API_KEY` |
| `VAANI_CONFIRM_S` | Action preview time; default 2.5 seconds |
| `VAANI_CHUNKED_STT=0` | Disable chunked dictation transcription |

Meeting mode uses the model configured in
[`meeting/gemini.py`](src/vaani/meeting/gemini.py), separately from
`VAANI_GEMINI_MODEL`. Provider model access and quotas can vary by account.

### 4. Test the environment

```bash
.venv/bin/python -c 'import gi; gi.require_version("Gtk", "4.0"); from gi.repository import Gtk; print("GTK4 OK")'
.venv/bin/python -c 'from vaani.audio import preflight; preflight(); print("Audio OK")'
.venv/bin/python -m vaani --record
```

The manual record command tests capture and transcription without global
shortcuts. Then start the desktop runtime in the foreground:

```bash
.venv/bin/python -m vaani
```

Keep the terminal open for the first shortcut test.

## Shortcuts and controls

These controls describe the current **Linux/X11 ThinkPad backend**.
Other keyboards and OS adapters can have different controls.
`Ctrl+Space` remains available to the desktop on this backend.

| Control | Action |
|---|---|
| Hold TrackPoint middle button | Record dictation |
| Release TrackPoint middle button | Stop dictation and process it |
| Double-press and hold middle button | Record an assistant command |
| Fn, then one middle-button click | Start hands-free dictation |
| Fn, then two middle-button clicks | Start hands-free assistant recording |
| Fn plus middle-button click while hands-free | Stop and process |
| Esc or pill cancel | Cancel dictation or its processing |
| Pill stop | Stop dictation and process it |
| TrackPoint middle button plus R | Start a meeting |
| TrackPoint middle button plus R twice | Stop a meeting and process it |
| Meeting pill pause/resume | Pause or resume meeting capture |
| Meeting pill stop | Save the meeting and start background transcription |

The Fn latch uses the detected keycode or `VAANI_LATCH_KEYCODE` override.
Set `VAANI_LATCH_KEYSYM=off` to disable it.
A single R tap during an active meeting does not stop it.
Releasing the TrackPoint button does not stop a meeting.

## Dictation and assistant behavior

Groq dictation preserves the words and applies light local cleanup. It does
not use the older smart paraphrase flow. Longer dictations can transcribe
chunks while capture continues. A failed chunk path falls back to the full
recording. At ten minutes, Vaani stops capture and processes the recording.

Gemini dictation is optional. It returns the provider transcript directly,
without the Groq answer-prefix or cleanup flow. Assistant mode can route that
transcript as a command.

Examples of assistant commands:

- “Open Firefox.”
- “Open my project in VS Code.”
- “Open text editor and write hello.”
- “Play a song on YouTube.”

Assistant tasks can reach external services. Hermes or Codex must be installed
and authenticated before delegated tasks can run. Vaani rejects agent requests that it identifies as write actions.
Codex skill runs use a selected skill and its MCP allowlist. Hermes uses its own configuration;
the Codex isolation rules do not apply to Hermes.

Experimental computer use can send the active window's accessibility data
and, when needed, its screenshot to a model provider. It requires separate
configuration and is not required for dictation.

## Meeting mode

1. Set `GEMINI_API_KEY` locally.
2. Hold the TrackPoint middle button and press R to start.
3. Use the separate red pill to pause or resume capture.
4. Press the pill stop button, or use TrackPoint plus R twice, to finish.
5. Wait for the notification that the transcript is ready.

The recording cap is four hours of captured time. Pauses do not count toward
that cap. A warning appears five minutes before the cap.

Meetings are saved in `Vaani Meetings` under the desktop Documents directory.
If that directory cannot be found, Vaani uses `~/Documents/Vaani Meetings`.
Each meeting has its own job state, audio, parts, and final `transcript.md`.
The transcript uses numbered speakers and timestamps. Speaker labels across
parts are inferred and can be wrong.

The worker transcribes in a separate process. The pill closes when capture
stops, and another meeting can start while the previous job processes.
The recorder captures the microphone and default output monitor. Preparation
mixes system audio for headphones; it uses the microphone track for speakers
to avoid duplicate voices.

Jobs save progress between stages. Quota errors defer work for a later retry.
Other failures retry with backoff, up to eight attempts. Failed jobs retain
their audio. Successfully transcribed audio and part files become eligible
for deletion after 30 days, when the worker next runs. The Markdown transcript
remains until you delete it.

## Data and privacy

Vaani uses cloud services. It is not an offline transcription tool.

| Data | Destination or local location |
|---|---|
| Dictation audio | Groq, or Google when Gemini dictation is enabled |
| Meeting audio parts | Google Gemini |
| Assistant text and context | Configured OpenRouter, Hermes, Codex, or computer-use provider |
| Raw and final dictation text | `~/.local/share/vaani/history.sqlite3` |
| Dictation chunk audio and text | `~/.local/share/vaani/sessions/` |
| Temporary dictation WAV | `~/.cache/vaani/audio/` |
| Meeting audio, provider responses, transcript, and job state | Documents directory / `Vaani Meetings/` |
| Operational logs | `~/.local/share/vaani/logs/` |
| Private site aliases | `~/.config/vaani/sites.json` |
| Browser history index and play memory | Local Vaani data directory |

These paths describe Linux. Other platforms use their OS data directories.
Transcript history is plaintext and has no automatic retention limit.
The final text remains on the clipboard; clipboard managers can keep copies.
Logs can contain transcript previews, task context, and file paths. Treat them
as private even though the main logger redacts some credential patterns.

Dictation archive folders older than 48 hours are removed when another
archived session starts. This is not a background deletion deadline.
Processing failures and startup recovery preserve available recordings there.
Cancel removes the active temporary WAV, but chunks already archived can
remain until the archive sweep. Meeting retention is separate.

Private credentials, browser data, recordings, transcripts, logs, and local
backups must stay outside the repository. `.gitignore` provides a second
check; it does not protect files that Git already tracks.

## Local releases and autostart

The release workflow creates a snapshot with its own environment under
`~/.local/share/vaani/release/versions/`. Autostart prefers the `current`
release over the work folder. Shared credentials stay outside that snapshot.

Install the launchers explicitly:

```bash
mkdir -p "$HOME/.local/bin" "$HOME/.config/autostart"
for script in vaani-start vaani-toggle vaani-assistant vaani-cancel vaani-release; do
  install -m 755 "scripts/$script" "$HOME/.local/bin/$script"
done
```

Set `VAANI_DEV_DIR` to the clone path when you use the release command from
another location. Review the default paths in `scripts/vaani-start` before
use on a new machine. The desktop file is in `data/vaani-autostart.desktop`;
set its `Exec` field to your installed launcher before you copy it to autostart.

```bash
VAANI_DEV_DIR="$PWD" vaani-release --status
VAANI_DEV_DIR="$PWD" vaani-release          # build and activate tested HEAD
vaani-release --rollback                   # activate previous release
```

A release command can restart Vaani. Wait until dictation and its processing
finish before promotion or rollback. Meeting capture has a separate systemd
scope and can survive a daemon restart, but this does not replace backups.
See [operations rules](docs/OPERATIONS.md).

## Development and checks

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -q -p no:cacheprovider
```

The keyring integration probe requires `VAANI_KEYRING_PROBE=1`.
X11 integration checks require a live X11 session. A skipped check does not
prove that integration works. Unit tests do not prove live provider access
or desktop behavior on every OS.

Before a push, scan the tracked files and the history that you will publish.
Review each finding without copying secret values into logs or reports.
Never add a real key to `.env.example` or a test fixture.

| Source | Purpose |
|---|---|
| `src/vaani/controller.py` | Dictation state, cancellation, and assistant routing |
| `src/vaani/audio.py` | Capture, validation, and recorder cleanup |
| `src/vaani/stt_chunks.py` | Chunked transcription during recording |
| `src/vaani/session_archive.py` | Local chunk archive and recording recovery |
| `src/vaani/meeting/` | Meeting capture, job state, preparation, transcription, and export |
| `src/vaani/platform/` | OS runtime, input, delivery, and UI adapters |
| `src/vaani/codex.py` | Hermes and Codex task execution |
| `src/vaani/jev.py` | Optional OpenRouter assistant routing |
| `scripts/vaani-release` | Pinned local releases and rollback |

Historical design documents can describe earlier behavior. Current runtime
source and tests take precedence. Keep local data backups separate from Git.
