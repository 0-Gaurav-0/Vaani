# Vaani Roadmap

This document is the working plan for turning Vaani from a **Linux/X11 personal
dictation tool** into an open-source **voice + remote operator** for your
computer.

Use it to:

- see what already works
- pick a feature without colliding with someone else
- open a focused branch per feature (suggested names below)
- keep dictation, OS control, agent actions, and remote access clearly separated

Historical design notes under `docs/superpowers/` are background only. When they
disagree with this roadmap or with current source/tests, **source, tests, and
this roadmap win**.

**Out of scope for Vaani:** workflow learning / watch-and-replay automation.
That belongs to a different project.

---



## Product north star

```text
Phone or local voice
        │
        ▼
   Intent API  (same commands everywhere)
        │
        ▼
   Operator brain  (parse → route → confirm → act)
        │
        ▼
   OS adapters  (macOS · Windows · Linux)
   mic · hotkeys · paste · apps · notify · allowlisted actions
```

Vaani should help you:

1. **Write faster** — dictate cleaned or literal text into any focused app
2. **Operate the machine** — open apps/sites, run safe OS actions by voice
3. **Delegate work** — hand harder tasks to a bounded agent (e.g. Codex CLI)
4. **Act remotely** — from a phone or another device, hit the same operator

Dictation is the on-ramp. The product is the operator layer.

---



## Status legend


| Status    | Meaning                                              |
| --------- | ---------------------------------------------------- |
| `done`    | Shipped in current Linux/X11 tree                    |
| `partial` | Exists but incomplete, buggy, or Linux-only          |
| `todo`    | Not started; ready to claim as a feature branch      |
| `later`   | Intentionally deferred after the core operator stack |


---



## How to claim work

1. Pick a **Feature ID** below (e.g. `P1-02`).
2. Open a branch named like the suggestion (e.g. `feat/p1-02-macos-adapter`).
3. Keep the PR scoped to that feature’s acceptance criteria.
4. Update this file’s status row when the PR merges (`todo` → `done` / `partial`).
5. Prefer depending only on features marked `done` or explicitly listed under
  **Depends on**.

Suggested branch prefix:


| Prefix          | Use                            |
| --------------- | ------------------------------ |
| `feat/<id>-…`   | User-facing capability         |
| `fix/<id>-…`    | Correctness bug                |
| `plat/<id>-…`   | OS adapter / platform plumbing |
| `remote/<id>-…` | Remote access layer            |
| `docs/<id>-…`   | Specs, roadmap, README-only    |


---



## Phase 0 — Stabilize the Linux baseline

Make the current Ubuntu/X11 app trustworthy before forking effort across OSes.


| ID    | Feature                                   | Status    | Suggested branch                                                                                 | Depends on | Acceptance criteria                                                                                                                                                                                                |
| ----- | ----------------------------------------- | --------- | ------------------------------------------------------------------------------------------------ | ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| P0-01 | Fix answer-prefix routing                 | `todo`    | `fix/p0-01-answer-prefix`                                                                        | —          | “Answer this / Only answer / Question” works in smart and literal modes; smart cleanup must not overwrite a generated answer; assistant mode must not send answer-prefix speech to app/site/Codex as the task body |
| P0-02 | Portable Esc + Ctrl+Super+Space hotkeys   | `todo`    | `fix/p0-02-portable-hotkeys`                                                                     | —          | Global Esc cancel and alternate assistant chord work without hardcoded reference-laptop keycodes/device names; document fallback behavior                                                                          |
| P0-03 | Wire indicator position restore           | `todo`    | `fix/p0-03-indicator-position`                                                                   | —          | Recording pill restores last position across restarts when the compositor allows it                                                                                                                                |
| P0-04 | History retention defaults                | `todo`    | `feat/p0-04-history-retention`                                                                   | —          | Configurable max entries or max age; default is bounded; plaintext warning stays in README                                                                                                                         |
| P0-05 | Codex result surface                      | `partial` | `feat/p0-05-assistant-result-ui`                                                                 | —          | Assistant/Codex output is readable beyond a fleeting `notify-send` (simple GTK window or scrollable notification path)                                                                                             |
| P0-06 | Linux Wayland backend | `done`    | `plat/p0-06-wayland-spike`                                                                       | —          | GlobalShortcuts-portal hotkeys + wl-clipboard/wtype delivery, auto-selected via `WAYLAND_DISPLAY`/`XDG_SESSION_TYPE`; clipboard-only degrade where the compositor has no synthetic-input path (GNOME/KDE); see `docs/install/linux.md`'s X11-vs-Wayland matrix |




### Already done on Linux/X11 (baseline inventory)

These are **not** open feature branches unless someone is fixing regressions:


| Area              | What works today                                      |
| ----------------- | ----------------------------------------------------- |
| Smart dictation   | `Ctrl+Space` → Groq Whisper → cleanup → paste         |
| Literal dictation | `Ctrl+Shift+Space` → raw transcript → paste           |
| Answer prefix     | Works reliably only in literal mode today (see P0-01) |
| Assistant routing | App launch → site open → Codex CLI fallback           |
| Apps / sites      | Built-in catalogs + `~/.config/vaani/sites.json`      |
| Delivery          | Clipboard + XTEST paste; terminal `Ctrl+Shift+V`      |
| History           | SQLite writes; **no UI**                              |
| Secrets           | GNOME Keyring + optional `GROQ_API_KEY`               |
| Autostart         | GNOME autostart via installed desktop entry           |
| Tests             | Unit + some integration coverage under `tests/`       |


---



## Phase 1 — Cross-platform foundation

Introduce a shared operator core and thin OS adapters. **Do not rewrite the
product in Electron/Tauri for this phase** unless a spike proves Python adapters
cannot deliver paste/hotkeys on Mac/Windows.

**Implementation docs (start here):**

- Spec: [docs/superpowers/specs/2026-07-26-cross-platform-adapters-design.md](docs/superpowers/specs/2026-07-26-cross-platform-adapters-design.md)
- Plan (sub-agent tasks): [docs/superpowers/plans/2026-07-26-cross-platform-adapters-plan.md](docs/superpowers/plans/2026-07-26-cross-platform-adapters-plan.md)
- Working branch: `plat/p1-cross-platform`

### Architecture target

```text
src/vaani/platform/
  protocol.py     # PlatformBundle + Protocols
  linux/          # current X11/Pulse/GTK extracted
  macos/          # Phase 1
  windows/        # Phase 1
```

Exact package layout can vary; the contract cannot:

```text
PlatformBundle
  recorder / hotkeys / target / delivery
  apps / browser / feedback / key_store
  run(controller)  # owns OS event loop
```


| ID    | Feature                                     | Status | Suggested branch             | Depends on     | Acceptance criteria                                                                                                                                                                          |
| ----- | ------------------------------------------- | ------ | ---------------------------- | -------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| P1-01 | Define `OSAdapter` protocol + extract Linux | `partial` | `plat/p1-cross-platform`     | —              | Linux path runs through an adapter interface; unit tests use fakes; no behavior regression on Ubuntu/X11                                                                                     |
| P1-02 | macOS adapter MVP                           | `partial` | `plat/p1-02-macos-adapter`   | P1-01          | Mic capture, at least one global hotkey path, clipboard paste into focused app, open app by bundle/name, notifications; documented permissions (Accessibility, Microphone, Input Monitoring) |
| P1-03 | Windows adapter MVP                         | `partial` | `plat/p1-03-windows-adapter` | P1-01          | Same MVP as macOS: mic, hotkey, paste, open app/URL, notify; documented Win10/11 assumptions                                                                                                 |
| P1-04 | Shared config/paths per OS                  | `partial` | `plat/p1-cross-platform`   | P1-01          | XDG on Linux, `~/Library/Application Support/Vaani` on macOS, `%APPDATA%\Vaani` on Windows; sites/history/logs land in the right place                                                       |
| P1-05 | Cross-platform install docs                 | `partial` | `docs/p1-05-install-matrix`  | P1-02 or P1-03 | README (or `docs/install/`) matrix: OS → deps → permissions → smoke test                                                                                                                     |
| P1-06 | CI matrix smoke                             | `todo` | `plat/p1-06-ci-matrix`       | P1-01          | Unit tests run on Linux CI; Mac/Windows jobs run pure-python tests + adapter fakes (live GUI tests remain opt-in/manual)                                                                     |
| P1-07 | Recording pill on macOS + Windows           | `partial` | `feat/p1-07-recording-indicator` | P1-02, P1-03 | Floating cancel/waveform/stop pill while recording; portable control file (no SIGUSR-only); Linux GTK unchanged; dictation works if pill fails to start |


**Phase 1 demo bar:** “Open Terminal/Cursor” and smart dictate-into-browser work on **at least two** of {Linux, macOS, Windows}.

---



## Phase 2 — Operator brain (OS-level actions)

Voice becomes a front door to **structured computer actions**, not only paste and
Codex.

### Intent categories


| Intent            | Examples               | Execution style                      |
| ----------------- | ---------------------- | ------------------------------------ |
| `dictate.smart`   | normal speech          | STT → cleanup → paste                |
| `dictate.literal` | code, names            | STT → paste                          |
| `answer`          | “Answer this…”         | STT → chat model → paste             |
| `os.open_app`     | “Open Cursor”          | Deterministic allowlist              |
| `os.open_url`     | “Open Gmail in Brave”  | Deterministic allowlist / sites.json |
| `os.system`       | “Mute”, “Volume 30”    | Deterministic allowlist              |
| `os.shell`        | “Run the tests”        | Allowlisted command map only         |
| `agent.task`      | “Fix the failing test” | Bounded agent runner + confirm       |



| ID    | Feature                             | Status    | Suggested branch             | Depends on            | Acceptance criteria                                                                                                                                    |
| ----- | ----------------------------------- | --------- | ---------------------------- | --------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| P2-01 | Intent router (structured)          | `todo`    | `feat/p2-01-intent-router`   | P1-01                 | Transcript maps to a typed intent; dictation/app/site/agent paths go through one router; tests cover conflicts (“open Claude” app vs website)          |
| P2-02 | Expand app catalog + user apps.json | `todo`    | `feat/p2-02-user-apps`       | P2-01                 | User can add private app aliases outside Git (mirror `sites.json`); built-ins remain safe executables only                                             |
| P2-03 | System toggles allowlist            | `todo`    | `feat/p2-03-system-toggles`  | P1-02 or P1-03, P2-01 | Mute/volume/lock (per-OS where available) via allowlisted implementations; unknown toggles refuse safely                                               |
| P2-04 | Allowlisted shell commands          | `todo`    | `feat/p2-04-shell-allowlist` | P2-01                 | `~/.config/vaani/commands.json` (or OS equivalent) maps phrases → argv; no free-form shell from raw speech                                             |
| P2-05 | Confirmation policy                 | `todo`    | `feat/p2-05-confirm-policy`  | P2-01                 | Destructive or agent intents require explicit confirm (hotkey, spoken “confirm”, or UI); dictation/open-app do not                                     |
| P2-06 | Agent runner interface              | `partial` | `feat/p2-06-agent-runner`    | P2-01, P2-05          | Codex remains one backend; interface allows another CLI/API later; timeout/cancel preserved; user MCP loading is an explicit opt-in flag (default off) |
| P2-07 | Focus / window targeting            | `todo`    | `feat/p2-07-window-focus`    | P1-02 or P1-03        | “Focus Slack” / “Switch to browser” where the OS allows; clear notification when unsupported                                                           |
| P2-08 | History UI                          | `todo`    | `feat/p2-08-history-ui`      | —                     | Browse/search/copy past transcripts and assistant results; read-only first is fine                                                                     |
| P2-09 | Custom vocabulary                   | `todo`    | `feat/p2-09-vocabulary`      | —                     | User word list biases cleanup/prompts for names and product terms                                                                                      |
| P2-10 | Voice snippets / macros             | `todo`    | `feat/p2-10-snippets`        | P2-01                 | Named snippets paste canned text without an agent                                                                                                      |


**Phase 2 demo bar:** Say “open Cursor”, “mute”, and “run the tests” (allowlisted) successfully; harder asks go through confirm → agent.

---



## Phase 3 — Remote access layer

Remote is **another input** to the same intent API. It must not invent a second
permission model.

```text
Phone / laptop browser
    → auth'd HTTPS or Tailscale-only HTTP
    → POST /v1/intent  { text | audio }
    → same Operator brain
    → OS adapter on the host machine
```


| ID    | Feature                           | Status | Suggested branch              | Depends on   | Acceptance criteria                                                                               |
| ----- | --------------------------------- | ------ | ----------------------------- | ------------ | ------------------------------------------------------------------------------------------------- |
| P3-01 | Local intent HTTP API             | `todo` | `remote/p3-01-intent-api`     | P2-01        | `POST /v1/intent` accepts text; returns structured result; binds to localhost by default          |
| P3-02 | Token auth + private network docs | `todo` | `remote/p3-02-auth-tailscale` | P3-01        | Bearer/token required; README documents Tailscale/LAN-only exposure; refuse default public bind   |
| P3-03 | Audio upload intent               | `todo` | `remote/p3-03-audio-intent`   | P3-01        | Phone can send a short audio clip; host runs existing STT → router                                |
| P3-04 | Minimal phone client              | `todo` | `remote/p3-04-phone-client`   | P3-02        | Simple mobile web page or lightweight app: text box + push-to-talk + last result                  |
| P3-05 | Remote session status             | `todo` | `remote/p3-05-session-status` | P3-01        | Client can see idle/recording/processing and cancel in-flight work                                |
| P3-06 | Hardening pass                    | `todo` | `remote/p3-06-hardening`      | P3-02, P2-05 | Rate limits, audit log for remote intents, confirm policy enforced for remote agent/shell equally |


**Phase 3 demo bar:** From a phone on Tailscale, “Open Terminal” and “run the tests” execute on the laptop with the same allowlists as local voice.

**Non-goals for Phase 3:** exposing raw unrestricted shell to the internet, UPnP/port-forward “just works” setups, or cloud relay that sees plaintext intents without an explicit design.

---



## Phase 4 — Polish and distribution

Do these after the operator + one remote path feel real.


| ID    | Feature                          | Status  | Suggested branch           | Depends on | Acceptance criteria                                                      |
| ----- | -------------------------------- | ------- | -------------------------- | ---------- | ------------------------------------------------------------------------ |
| P4-01 | Settings UI                      | `todo`  | `feat/p4-01-settings-ui`   | P1-04      | Hotkeys, models, mic, Brave/Chrome default, assistant cwd, remote toggle |
| P4-02 | Push-to-talk mode                | `todo`  | `feat/p4-02-ptt`           | P1-01      | Hold-to-talk option alongside toggle                                     |
| P4-03 | Streaming / partial STT          | `later` | `feat/p4-03-streaming-stt` | P1-01      | Interim text while speaking (provider permitting)                        |
| P4-04 | Offline / local Whisper fallback | `later` | `feat/p4-04-local-whisper` | P1-01      | Falls back when Groq is unreachable; quality/latency documented          |
| P4-05 | Packaged installers              | `todo`  | `plat/p4-05-packaging`     | P1-05      | `pipx`/`uv tool` and/or platform packages; one-shot launcher install     |
| P4-06 | Multi-provider STT/LLM           | `later` | `feat/p4-06-providers`     | P2-06      | Groq remains default; provider interface for alternatives                |
| P4-07 | Accessibility pass               | `todo`  | `feat/p4-07-a11y`          | P2-08      | Keyboard-usable history/settings; screen-reader labels where applicable  |


---



## Feature map (pick-and-choose view)

Use this when deciding “what should I build next?”


| If you care about…          | Claim these first                     |
| --------------------------- | ------------------------------------- |
| Trust / bugfixes on Linux   | P0-01, P0-02                          |
| Mac support                 | P1-01 → P1-02 → P1-04 → P1-05         |
| Windows support             | P1-01 → P1-03 → P1-04 → P1-05         |
| “Open / do things” by voice | P2-01 → P2-02 → P2-03 → P2-04 → P2-05 |
| Better agent delegation     | P2-05 → P2-06 → P0-05                 |
| Phone controlling laptop    | P2-01 → P3-01 → P3-02 → P3-04         |
| Dictation quality           | P2-08, P2-09, P2-10, P4-02            |


Parallelism that usually does **not** conflict:

- P0-01 (controller) || P0-02 (hotkeys) || P2-08 (history UI)
- P1-02 (macOS) || P1-03 (Windows) after P1-01 merges
- P3-01 (API) || P2-03 (system toggles) after P2-01 merges

---



## Suggested weekend sequencing

For a small open-source group this weekend:

1. **Merge P1-01** — adapter extraction (unblocks everyone)
2. In parallel:
  - Person A: **P1-02 or P1-03** (one new OS)
  - Person B: **P2-01** (intent router) + **P0-01** (answer bug)
3. If time: **P3-01 + P3-02** localhost intent API behind a token
4. Demo script:
  - Local: “Open Cursor” on two OSes
  - Local: dictate a sentence into a browser
  - Remote: phone text “Open Terminal” over Tailscale/LAN

---



## Safety and privacy rules (apply to every feature)

- No free-form shell from raw speech; allowlists only.
- Remote must default to localhost or private network + token.
- Agent backends run with user permissions; destructive work needs confirm.
- Do not commit keys, private URLs, transcripts, audio, or machine paths.
- Cloud STT/LLM boundaries stay documented in the README whenever providers change.
- Prefer failing closed (notify + log) over partial unsafe execution.

---



## Documentation companions


| Doc                                      | Role                                                 |
| ---------------------------------------- | ---------------------------------------------------- |
| [README.md](README.md)                   | Install, operate, troubleshoot **current** Linux app |
| [ROADMAP.md](ROADMAP.md)                 | This file — planned features and branch strategy     |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | Safe restart / ops rules                             |
| `docs/superpowers/`                      | Historical specs/plans; not authoritative            |


When a feature needs a design longer than a PR description, add
`docs/superpowers/specs/YYYY-MM-DD-<feature-id>-design.md` and link it from the
feature row before implementation sprawls.

---



## Open decisions (resolve when the feature is claimed)

Record the decision in the feature PR; do not block the whole roadmap on them.

1. **Agent default:** Codex CLI only vs pluggable runner in P2-06.
2. **Confirm UX:** spoken “confirm”, second hotkey, or GUI prompt.
3. **Remote transport:** Tailscale-first HTTP vs bundled WireGuard-like peer UI.
4. **UI toolkit on Mac/Windows:** keep GTK where possible vs native notifications-only MVP.
5. **Packaging:** `uv tool install` first vs platform-native installers first.

---



## Changelog of roadmap intent


| Date       | Note                                                                                                            |
| ---------- | --------------------------------------------------------------------------------------------------------------- |
| 2026-07-25 | Initial roadmap: cross-platform adapters, operator intents, remote layer; explicitly excludes workflow learning |


