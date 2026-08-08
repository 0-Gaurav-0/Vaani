# Vaani map — where everything lives

Plain-language guide to names and paths on this machine.  
Updated: 2026-08-07.

## One-sentence picture

**Vaani** = the voice app that listens (TrackPoint / snap / “hey Vaani”) and does local actions (dictation, play/pause, open apps/sites).  
**Hermes** = the AI agent Vaani hands hard questions to.  
**This Cursor window** = editing the Vaani *code* under `~/Vaani`.

---

## Quick “I want to…”

| You want… | Go here |
|-----------|---------|
| Edit Vaani’s code (this session) | `~/Vaani` → actually `…/control/Vaani-main` |
| Start / restart the voice app | `~/.local/bin/vaani-start` |
| Read today’s voice logs | `~/.local/share/vaani/logs/vaani.log` |
| See what Hermes did for voice tasks | Hermes app → project **Vaani agent**, or folder `~/vaani-agent/vani-task` |
| Change “Hermes must only read” rules | `~/.agents/skills/vaani/SKILL.md` and `~/vaani-agent/vani-task/AGENTS.md` |
| Say “open Vaani project” | Opens Cursor on `~/Vaani` (this workspace) |

---

## A. Names that sound alike

| Name you hear | What it actually is |
|---------------|---------------------|
| **Vaani** / **Vani** / **Wani** | The voice product (and STT spellings of its name) |
| **Vaani-main** | The *active* source tree you run and develop (`python -m vaani`) |
| **Vaani** (folder under `control/`) | Older / alternate tree next to Vaani-main — not what `vaani-start` uses |
| **vaani-stable** | Snapshot / backup tree — not the live runner |
| **~/Vaani** | Shortcut symlink → whole `control` repo parent; Cursor opens this for “Vaani project” |
| **vani-task** | Hermes working folder for voice handoffs (typo-ish spelling is intentional historically) |
| **Vaani agent** | Hermes *project name* for those handoffs (UI label) |
| **vaani skill** | Hermes skill file: read-only + voice rules when call is from Vaani |
| **Hermes** | Separate agent app/CLI; Vaani starts it with `hermes -z` |
| **Cursor** | This editor; “open Vaani project” launches Cursor on `~/Vaani` |

---

## B. Code & repos (what you edit)

| Path | Plain description |
|------|-------------------|
| `/home/gaurav/Vaani` | Symlink to `…/03-REPOSITORIES/control`. Your Cursor “Vaani project” root. |
| `…/control/Vaani-main/` | **Live product code.** `vaani-start` runs `.venv/bin/python -m vaani` from here. |
| `…/control/Vaani-main/src/vaani/` | Python package: hotkeys, STT, assistant routing, media, browser, Hermes handoff. |
| `…/control/Vaani-main/tests/` | Unit/integration tests. |
| `…/control/Vaani-main/docs/` | Design notes, plans, this map. |
| `…/control/Vaani-main/scripts/` | Helper scripts shipped with the repo. |
| `…/control/Vaani/` | Sibling tree (not the one autostart uses). Easy to confuse with Vaani-main. |
| `…/control/vaani-stable/` | Frozen / stable copy — don’t assume it’s running. |
| `…/control/backups/` | Backup artifacts under the control repo. |

---

## C. Running the voice app (process & launchers)

| Path | Plain description |
|------|-------------------|
| `~/.local/bin/vaani-start` | **Main launcher.** Starts Vaani-main’s venv; sets Hermes cwd to `~/vaani-agent/vani-task`. |
| `~/.local/bin/vaani-toggle` | Shortcut helper to toggle recording (dictation). |
| `~/.local/bin/vaani-assistant` | Shortcut helper for assistant mode. |
| `~/.local/bin/vaani-cancel` | Shortcut helper to cancel. |
| `~/.local/bin/vaani-history-sync` | Syncs Chrome/Brave history into Vaani’s site catalog. |
| `~/.config/autostart/com.gaurav.vaani.desktop` | Starts Vaani when you log into the desktop. |
| `~/.config/systemd/user/vaani-history-sync.*` | Timer/service: refresh browser-history catalog every few hours. |
| Process: `.venv/bin/python -m vaani` | The actual Vaani process (check with `pgrep -af 'python -m vaani'`). |

---

## D. Runtime data (logs, memory, history)

All under `~/.local/share/vaani/` unless noted.

| Path | Plain description |
|------|-------------------|
| `logs/vaani.log` | **Primary debug log** (STT, routes, media, handoffs). |
| `logs/autostart.log` | stdout/stderr from `vaani-start` / autostart. |
| `history.sqlite3` | Dictation / assistant history Vaani stores locally. |
| `history_catalog.sqlite` | Indexed browser history for “open that site I used…” style matching. |
| `handoffs.jsonl` | Recent Hermes handoffs (for “continue that session”). |
| `memory/` | Local memory bits Vaani keeps. |
| `~/.config/vaani/` | Small runtime config (e.g. indicator state). Optional `apps.json` for custom app aliases. |

---

## E. Hermes / agent side (AI that can use tools)

| Path / name | Plain description |
|-------------|-------------------|
| `hermes` CLI (`~/.local/bin/hermes`) | Agent binary Vaani calls for “ask Vaani…”, skills, etc. |
| Hermes UI project **Vaani agent** | Where voice-started chats show up in Hermes Desktop. |
| `~/vaani-agent/vani-task/` | **Working directory** for those sessions (files Hermes sees as project root). |
| `~/vaani-agent/vani-task/AGENTS.md` | Standing instructions for that Hermes project (Basecamp-first reads, read-only). |
| `~/.agents/skills/vaani/SKILL.md` | **Vaani skill** — preloaded on every voice handoff (`--skills vaani`). Read-only + voice style. |
| `~/.hermes/skills/vaani` | Symlink to the same skill so Hermes can load it. |
| `~/.agents/skills/basecamp/` | Basecamp CLI skill (work todos/status — read via agent). |
| `~/.hermes/` | Hermes app data (config, sessions DB, caches). Not Vaani’s code. |

**Rule of thumb:** Vaani itself opens sites / presses media keys. Anything that needs “thinking + tools” goes to Hermes under **Vaani agent** + `vani-task`, with the **vaani** skill attached.

---

## F. Cursor / this chat session

| Path | Plain description |
|------|-------------------|
| Workspace `~/Vaani` | What “open Vaani project” opens in Cursor. |
| `~/.cursor/projects/home-gaurav-Vaani/` | Cursor’s metadata for this workspace (terminals, transcripts, canvases). |
| `…/canvases/` | Interactive Canvas files for this chat project. |
| `…/agent-transcripts/` | Saved chat transcripts. |
| `…/terminals/` | Live terminal session captures Cursor uses. |

---

## G. How a voice request flows (mental model)

1. **Gesture/wake** — TrackPoint hold, double-press+hold, finger-snap, or “hey Vaani”.  
2. **Vaani process** — records mic → Groq STT → router / fast paths.  
3. **Local fast paths** (no Hermes) — volume, media play/pause/next, open app/site, open Vaani project, YouTube play.  
4. **Hermes handoff** — tagged `[source: vaani]`, `--skills vaani` (+ basecamp when matched), cwd = `vani-task`.  
5. **Results** — pill / notification / browser / Cursor window; **no TTS yet** (Vaani does not speak back).

---

## H. Useful env vars (optional)

| Variable | Meaning |
|----------|---------|
| `VAANI_ASSISTANT_CWD` | Hermes working dir (default `~/vaani-agent/vani-task`). |
| `VAANI_AGENT_BIN` | Override agent CLI (default `hermes`). |
| `VAANI_WAKE_ASSISTANT` | `0` disables always-on “hey Vaani” listening. |
| `VAANI_SNAP_ASSISTANT` | `0` disables finger-snap assistant. |
| `VAANI_HANDOFF_TIMEOUT` | Max seconds for a Hermes handoff job. |

---

## I. Don’t confuse these three “projects”

1. **Cursor project “Vaani”** — editing code (`~/Vaani` / Vaani-main).  
2. **Hermes project “Vaani agent”** — AI sessions from voice (`vani-task`).  
3. **Running app Vaani** — background listener started by `vaani-start`.

They share a name on purpose, but they are three different things.
