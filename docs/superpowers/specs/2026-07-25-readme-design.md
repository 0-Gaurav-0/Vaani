# Vaani README Design

**Date:** 2026-07-25
**Audience:** The primary user maintaining, reinstalling, operating, and
developing Vaani on Ubuntu

## Goal

Create one comprehensive `README.md` that is sufficient to understand Vaani,
install it on a fresh supported machine, configure credentials safely, operate
every mode, diagnose common failures, run the test suite, and recover the
installation without relying on chat history.

## Scope

The README will cover:

- product purpose, feature summary, and current Linux/X11 support boundary;
- verified system and Python prerequisites;
- installation and first-run checks;
- Groq credential storage through the system keyring;
- manual launch and GNOME autostart;
- smart and literal dictation, answer-prefix behavior, and Codex assistant mode;
- all implemented shortcuts and cancellation controls;
- private site aliases and assistant workspace configuration;
- data locations, transcript history, logs, temporary audio, and privacy;
- architecture, processing flow, and a concise source-module map;
- safe restart rules;
- symptom-oriented troubleshooting;
- development, testing, and secret-scanning commands;
- backup and recovery steps;
- current limitations and security notes.

The README will not claim Windows, macOS, Wayland, packaging, or workflow
learning support that the current repository does not implement.

The README will explicitly document current behavior rather than presenting
known gaps as working features:

- answer prefixes currently deliver the generated answer through literal
  dictation only; smart mode and assistant mode do not reliably deliver that
  answer because of the current controller flow;
- historical plans and specifications may be obsolete, so current source and
  executable tests are authoritative;
- the effective recording auto-stop is five minutes, even though the WAV
  validator accepts files up to ten minutes;
- recordings shorter than 250 milliseconds are rejected;
- stopping moves the application into processing, while cancellation discards
  the active operation rather than delivering partial text.

## Installation Contract

The README will provide one exact, locally validated installation sequence for
Ubuntu 22.04 on GNOME/Xorg x86_64. It will include:

- the required APT packages: `pulseaudio-utils`, `xinput`, `xclip`,
  `python3-gi`, `gir1.2-gtk-4.0`, `libgtk-4-1`, `libnotify-bin`,
  `gnome-keyring`, and the appropriate Python venv package;
- Python 3.10 or newer and `uv`;
- a virtual environment created with access to system site packages so
  PyGObject and the recording widget remain available;
- dependency synchronization and package installation;
- installation of all four launcher scripts on the desktop session's `PATH`;
- installation of the GNOME autostart desktop file;
- environment handling for source checkouts through `VAANI_PROJECT_DIR`;
- first-run checks for X11, microphone access, keyring access, hotkey
  registration, widget visibility, transcription, and paste delivery.

The README will distinguish Python package installation from the separate
launcher and desktop-file installation because `pyproject.toml` does not bundle
those files.

## Assistant Capability and Security Boundary

The README will state that Codex assistant requests:

- start new ephemeral sessions;
- use `--ignore-user-config`, so the user's configured MCP servers, skills, and
  other Codex configuration are not loaded by this implementation;
- use low reasoning effort and a 30-second timeout;
- run in `VAANI_ASSISTANT_CWD`, or the user's home directory when it is unset;
- require an already installed and authenticated Codex CLI;
- run with the desktop user's operating-system permissions;
- inherit the desktop environment except that `OPENAI_API_KEY` and
  `GROQ_API_KEY` are removed.

The README will not imply that the current assistant automatically loads MCPs,
reuses an interactive Codex session, or has broader permissions than the
desktop user.

## Shortcut Compatibility

The shortcut table will distinguish:

- `Ctrl+Space` smart dictation and `Ctrl+Shift+Space` literal dictation through
  X11 passive grabs;
- `Ctrl+Alt+Space` assistant mode through the X11 passive grab;
- `Ctrl+Super+Space` through the separate `xinput` polling path;
- global `Esc` cancellation through that polling path;
- signal-based launcher scripts and their process-name matching requirement.

The README will warn that the polling path currently assumes specific keycodes,
looks for one keyboard device name, and falls back to device ID 11. It will
provide commands for inspecting the actual X11 keyboard when the alternate
assistant shortcut or global Escape does not work.

## Privacy and Retention

The README will state concretely that:

- recorded audio is uploaded to Groq for transcription;
- smart cleanup and answer-prefix behavior send text to a Groq chat model;
- raw and final transcripts are stored in plaintext SQLite history without a
  configured retention limit;
- delivered text remains on the system clipboard;
- audio is normally deleted after transcription, while a hard crash may leave a
  temporary file until the next startup sweep;
- `/tmp/vaani-amplitude` contains a scalar level rather than recorded audio;
- logs are sanitized by design but should still be handled as private runtime
  data;
- backups must exclude plaintext API keys and should rely on restoring the key
  through the operating-system keyring.

## Accuracy Rules

Every shortcut, command, path, package, environment variable, and behavior must
be checked against the current source or executed locally where safe.

Commands will:

- use repository-relative paths;
- contain placeholders rather than real credentials;
- avoid machine-specific usernames and private URLs;
- distinguish required dependencies from optional Codex assistant tooling;
- avoid restarting the currently running stable Vaani service during
  validation.

Unknown or environment-dependent behavior will be labeled instead of guessed.

Current source and executable tests override older planning and design
documents when they disagree.

## Structure

The README will begin with a concise overview and quick-start path, followed by
progressively deeper operating and development material. Tables will be used
only where they make shortcuts, modes, paths, or troubleshooting mappings
easier to scan.

Code examples will be copyable shell commands. Security-sensitive examples will
use environment variables or keyring prompts and will never place a real key in
Git.

## Verification

Before publication:

1. Create the documented development environment and execute each safe README
   shell block from that environment; syntax-check blocks that cannot safely be
   executed.
2. Cross-check behavior against `audio.py`, `config.py`, `controller.py`,
   `codex.py`, `delivery.py`, `feedback.py`, `groq.py`, `history.py`,
   `hotkeys.py`, `indicator.py`, `secrets.py`, `sites.py`, and launcher scripts.
3. Run the complete test suite from the documented environment and identify
   opt-in keyring and live-X11 checks when reporting skipped tests.
4. Run `detect-secrets` and explicit credential-prefix/private-information
   patterns against the final tracked files and reachable Git history.
5. Inspect the binary fixture independently instead of treating text scanning
   as sufficient.
6. Confirm the GitHub repository remains private and the original stable Vaani
   process remains unchanged.

## Success Criteria

- A fresh reader can install and start Vaani without prior chat context.
- Every implemented operating mode and shortcut is documented accurately,
  including compatibility constraints and known broken behavior.
- Troubleshooting starts from observable symptoms and gives safe checks.
- No credential, private URL, personal machine path, or internal operational
  information appears in the README or commit.
- Tests and publication-safety scans pass before the update is pushed.
