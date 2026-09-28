# Vaani on Linux

Linux is the production baseline: PulseAudio/`parec` capture, global hotkeys
(X11 passive grabs, or the GlobalShortcuts portal on Wayland), clipboard
paste, GTK/tk recording pill, and desktop notifications.

## Requirements

- Ubuntu 22.04+ (or similar) with **X11**, or a **Wayland** session with a
  GlobalShortcuts-capable portal (GNOME 45+, KDE Plasma 6+)
- Python 3.10+
- [uv](https://github.com/astral-sh/uv) (recommended) or pip
- PulseAudio / PipeWire with `parec` and a default source
- Groq API key (`GROQ_API_KEY` or `vaani` keyring entry)
- Optional: [Codex CLI](https://github.com/openai/codex) on `PATH` for assistant answers
- Optional: `paplay` / `notify-send` for sound and desktop cues
- Optional: `tkinter` for the bottom recording pill (falls back gracefully)

## Install

```bash
cd /path/to/Vaani
python3 -m venv .venv
source .venv/bin/activate
uv pip install -e '.[linux]'
# or: pip install -e '.[linux]'
```

System packages (Debian/Ubuntu examples):

```bash
sudo apt install pulseaudio-utils python3-tk libnotify-bin
# GTK4 legacy pill (optional): gir1.2-gtk-4.0
```

## Configure

```bash
export GROQ_API_KEY='gsk_...'
```

Data lands under XDG paths (typically `~/.local/share/vaani`,
`~/.cache/vaani`, `~/.config/vaani`).

While recording, a compact black pill sits at the **bottom** of the screen:
grey **X** (cancel), live waveform, white **✓** (stop & paste). Horizontal drag
only. Spawned via `python -m vaani.platform.linux.indicator_app` (shared tk).

| IPC | Path / env |
|---|---|
| Amplitude | `~/.cache/vaani/amplitude` (`VAANI_AMPLITUDE_PATH`) |
| Stop/cancel control | `~/.cache/vaani/indicator_control.json` (`VAANI_INDICATOR_CONTROL`) |
| Phase (recording/processing) | `~/.cache/vaani/indicator_phase` (`VAANI_INDICATOR_PHASE`) |
| Pill X position | `~/.config/vaani/indicator.json` |

On startup Vaani reaps orphan pill processes from a previous crash.

## Hotkeys (hold-to-talk)

Chords use the **Ctrl+Space** family (X11 passive grabs via `python-xlib`).

| Chord | Action |
|---|---|
| `Ctrl+Space` | **Hold** for smart dictation (release = stop) |
| `Ctrl+Shift+Space` | **Hold** for literal dictation |
| `Ctrl+Alt+Space` | **Hold** for assistant |
| `Esc` | Cancel in-flight recording / request |

The bottom pill appears while the chord is held. On release it switches to a
**processing** animation (pulsing dots) until paste finishes, then vanishes.
New dictation hotkeys are ignored while processing (Esc still cancels).
The microphone (`parec`) is opened only while recording (`event=mic_open` /
`event=mic_close` in the log) — not for the whole time Vaani is running.

If a chord is already claimed by another app, registration fails — free the
shortcut or change the other app’s binding.

## X11 vs Wayland

Vaani auto-detects the session (`WAYLAND_DISPLAY`, falling back to
`XDG_SESSION_TYPE`) and picks a backend — no configuration needed.

| Session | Global hotkeys | Auto-paste | Notes |
|---|---|---|---|
| **X11** | Supported | Yes (XTEST) | Primary / most-tested path |
| **Wayland — GNOME 45+, KDE Plasma 6+** | Supported via the `org.freedesktop.portal.GlobalShortcuts` portal | No — clipboard only, press Ctrl+V yourself | First run shows a system dialog to assign each shortcut's key combo; there's no Esc-cancel equivalent on the portal |
| **Wayland — Sway, Hyprland (wlroots)** | Supported via the same portal | Yes, via `wtype` (needs the `wtype` binary installed) | Falls back to clipboard-only if `wtype` is missing or the compositor rejects it |
| **Wayland — no portal / older xdg-desktop-portal** | Not supported | — | Vaani logs `event=startup_failure` and exits; install/upgrade `xdg-desktop-portal` or use an X11 session |

Check your session:

```bash
echo "$XDG_SESSION_TYPE"
echo "$WAYLAND_DISPLAY"
```

Startup always prints which backend it picked (`[vaani] session=…`). If the
portal doesn't implement GlobalShortcuts, Vaani prints the D-Bus error and
exits with a clear message rather than silently doing nothing.

## Run

```bash
python -m vaani --record   # mic + Groq smoke (no paste)
python -m vaani            # full daemon (hold-to-talk + paste)
python -m vaani --debug    # richer console + log hints
```

Log file path is printed at startup (`[vaani] log file: …`).

## Smoke checklist (live Linux machine)

- [ ] `uv pip install -e '.[linux]'` succeeds
- [ ] `python -m vaani --record` prints a transcript after Enter
- [ ] Hold `Ctrl+Space` → pill + waveform → release → processing dots → paste
- [ ] New hold during processing is ignored; Esc cancels
- [ ] Mic is not held open while idle (`event=mic_close` after stop)
- [ ] Literal (`Ctrl+Shift+Space`) skips cleanup; smart uses soft cleanup
- [ ] Changing focus mid-transcription yields clipboard-only when applicable
- [ ] Orphan pill after kill -9 is cleaned on next startup

## Notes

- Stop/cancel from the pill uses the control file (and optional SIGUSR on Linux).
- Feedback cues use `paplay` / FreeDesktop sounds when present, else `notify-send`
  / beep.
- Wayland-native global shortcuts are shipped (portal-based); auto-paste
  parity with X11 only exists on wlroots compositors — GNOME/KDE Wayland is
  clipboard-only by the compositor's own design, not a Vaani limitation.
