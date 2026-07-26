# Vaani assistant — command use cases (detailed)

Branch: `explore/assistant-use-cases`
Status: use-case definition. Detailed enough to derive a feature stack; **not** an implementation plan.
Supersedes: the scope-only version of this file (2026-07-26 morning).

---

## 0. How to read this document

| Section | Answers |
|---|---|
| 1. Vision & modes | What assistant mode *is*, and the three sibling modes |
| 2. Reference products | What we borrow and what we reject, with sources |
| 3. Today's behavior | What the code already does (the honest baseline) |
| 4. The escalation ladder | Which mechanism handles a command, and in what order |
| 5. Contracts | Intent, Context, Verb, Result, Risk — the types everything else hangs off |
| 6. Use-case template | The 14 fields that make a use case "defined" |
| 7–10. Category deep dives | System / Terminal / CLI / UI, worked in detail |
| 11. Guide mode | The screen-pointing use cases (new) |
| 12. Cross-cutting subsystems | Confirm, dry-run, undo, capability matrix, audit |
| 13. Feature stack | Layers → modules → feature IDs → build slices |
| 14. Verification strategy | How each layer is tested |
| Appendix A | Full command corpus → verb mapping |
| Appendix B | Open decisions |

A use case is **not defined** until it has all 14 fields from §6. Sections 7–11 either fill them in or say `spike`.

---

## 1. Vision and the four modes

**Hold the assistant hotkey → give an order → Vaani performs it on the machine**, on macOS, Windows, and Linux, escalating to a user-selected brain only when a cheaper route can't do it.

Vaani has four voice modes. They are distinguished by **what counts as success**, not by phrasing.

| Mode | Hotkey (today) | Success is | Example |
|---|---|---|---|
| `dictate.smart` | `Ctrl+Space` | Cleaned text in the focused field | "we should ship this on friday" |
| `dictate.literal` | `Ctrl+Shift+Space` | Exact text in the focused field | "kebab dash case" |
| `answer` | Answer-prefix inside dictation | A generated answer pasted into the field | "Answer this: what's 15% of 240" |
| **`act`** | `Ctrl+Alt+Space` | **Machine state changed** | "free port 3000" |
| **`guide`** | `act` hotkey + question-shaped speech | **A pointer on screen + one spoken line** | "how do I turn off notifications here" |

### 1.1 The boundary that used to be wrong

The previous version of this doc said spoken questions ("how do I free port 3000?") were out of scope. That deleted the single most common assistant utterance. The correct split:

- Question about **the machine or the screen in front of you** → `guide` (point at it; optionally offer to do it).
- Question about **general knowledge or code** → `answer` (paste) or a browser search verb.
- **Imperative** → `act`.

So: "how do I free port 3000?" → `guide` responds *"I can do it — 3000 is held by node, PID 41233. Confirm?"* That is one utterance, one pointer, one offer. It is never a spoken essay.

### 1.2 Classifier rules (`act` vs `guide`)

Run on the normalized transcript, before intent matching:

1. Answer-prefix present (`Answer this` / `Only answer` / `Question`) → `answer`. Must never reach the verb router. (See ROADMAP `P0-01`.)
2. Leads with an interrogative (`how|what|where|why|which|can i|is there|do i`) **and** the matched verb is non-mutating → `guide`.
3. Leads with an interrogative **and** the matched verb is mutating → `guide` with an **offer**: state the finding, name the verb, request confirm. Never mutate on a question.
4. Otherwise → `act`.
5. No verb match at any rung → `guide` fallback: say what Vaani understood and what it can't do. Never silently no-op.

### 1.3 Explicitly still out of scope

- Long "what can you do" FAQ narration. The answer to that is a settings/history UI, not speech.
- Continuous screen watching. Screen is read **only** on hotkey press (Clicky's privacy posture — adopted deliberately, see §2).
- Workflow learning / watch-and-replay (ROADMAP non-goal).

---

## 2. Reference products — what we take, what we drop

Sources reviewed for this spec:

- [HeyClicky](https://www.heyclicky.com/) — Mac-native, screen-aware, push-to-talk on Control+Option, agent mode via the phrase "clicky agent"; screenshots taken only on hotkey and never stored; free / $20 / $100 tiers gated on *agent messages*, not dictation. ([overview](https://www.zeitgeist.bot/company/heyclicky), [demo writeup](https://explainx.ai/blog/heyclicky-voice-control-mac-gpt-realtime-2-demo-2026))
- [OpenClicky](https://github.com/jasonkneen/openclicky) — open-source Swift/SwiftUI menu-bar equivalent: explicit routing hierarchy, `[POINT:x,y:label]` / `[TYPE:x,y:label]` overlay protocol, a local control bridge on `http://127.0.0.1:32123`, bundled skills, child-worker spawning, Accessibility + Microphone + Screen Recording permissions.
- [heyCLI](https://www.heycli.com/) — "your copilot for linux commands": natural language in, a **Linux command** out.
- [basecamp/hey-cli](https://github.com/basecamp/hey-cli) — typed verb catalog (`hey box`, `hey todo add`, `hey timetrack start`), `--json` on every command, keyring credentials with file fallback, TUI and CLI over one API, and `hey skill install` to expose the same verbs to an agent.

### 2.1 Adopted

| # | Borrowed idea | From | Consequence for Vaani |
|---|---|---|---|
| A1 | **Escalation ladder, cheapest rung first, computer-use last** | OpenClicky | §4. Vaani already has a 3-rung version hardcoded in the controller; formalize it to 7 rungs and report which rung ran. |
| A2 | **Typed verb catalog with `--json`, one catalog / many callers** | hey-cli | §5.3. Voice, remote HTTP, `vaani do …` CLI, and the agent brain all call the same registry. Makes every use case testable without a microphone. |
| A3 | **Materialize the command, then confirm** | heyCLI | §12.2. For rung 6/7 and anything shell-shaped, the argv is the artifact the user approves. `--dry-run` is a modifier on every verb, not a separate mode. |
| A4 | **Screen read only on hotkey; nothing stored** | HeyClicky | §11. Screen capture is a context provider gated on the same keypress that starts recording. No frames on disk, no background capture. |
| A5 | **On-screen pointer as an output primitive** | OpenClicky | §11.2. Overlay verbs (`point`, `caption`, `tour`) extend the existing indicator control file into an output bus. |
| A6 | **Wake-phrase escalation for expensive work** | HeyClicky ("clicky agent") | §12.5. "Vaani, agent: …" forces rung 6 and skips the router. Explicit user intent to spend time and money. |
| A7 | **Local control bridge exposing output primitives, not just input** | OpenClicky | §13 L8. Phase 3's `POST /v1/intent` gains sibling endpoints for pointer/caption/status/events. |
| A8 | **Session continuity: continue vs spawn** | OpenClicky (`sessions_send` / `sessions_spawn`) | §12.6. "and now also push it" continues the last agent session instead of starting a cold one. |
| A9 | **Hands-free confirm path** | OpenClicky (AirPods gestures) | §12.2. Confirm must be reachable without touching the keyboard, because the user's hands may be off it. |
| A10 | **Capability packs / skills, opt-in** | OpenClicky, hey-cli | §12.7. `git`, `docker`, `k8s`, `browser-cdp` ship as installable packs, not one monolith allowlist. |

### 2.2 Rejected

| Rejected | Why |
|---|---|
| Agent-first routing (speech → LLM → shell, heyCLI-style as the default path) | Blows the latency budget for the 80% case and turns raw speech into shell. ROADMAP rule: no free-form shell from raw speech. Deterministic verbs first. |
| Continuous screen awareness | Privacy cost, battery cost, and unnecessary — hotkey-gated capture covers the use cases. |
| Cloud-account requirement / login-gated tiers | Vaani is local-first; brains are bring-your-own-key. |
| Metering by "agent messages" | Not a product concern here, but it confirms the cost boundary is at rung 6 — worth surfacing rung cost in history. |
| macOS-only overlay design | Every use case must state its Linux and Windows story or be marked `unsupported` explicitly (§12.4). |

---

## 3. Today's behavior (the honest baseline)

Grounded in current source, so the stack can be described as a refactor rather than a greenfield.

| Concern | Today | Where |
|---|---|---|
| Assistant routing | Inline in the controller: `resolve_app` → `resolve_site` / browser allowlist → `CodexRunner.run` fallback | [controller.py:251-307](../../../src/vaani/controller.py#L251-L307) |
| App resolution | Requires an action word (`open\|launch\|start\|show`); rejects utterances containing `website`, `web app`, `in brave`, `in chrome`, `browser` | [apps.py:48-57](../../../src/vaani/apps.py#L48-L57) |
| App catalogs | Per-OS. Linux = executables + `shutil.which`; macOS = `open -a` with `native_name` | [apps.py:17](../../../src/vaani/apps.py#L17), [platform/macos/apps.py:11](../../../src/vaani/platform/macos/apps.py#L11) |
| Site resolution | Alias table + `~/.config/vaani/sites.json`; browser preference parsed from the phrase | [sites.py:70-78](../../../src/vaani/sites.py#L70-L78) |
| Browser open | Exact-match phrase allowlist, no free-form suffixes | [controller.py:350-373](../../../src/vaani/controller.py#L350-L373) |
| Agent runner | `codex exec --ephemeral --ignore-user-config`, 30s timeout, cwd from `VAANI_ASSISTANT_CWD` or `$HOME`, API keys stripped from env, session-leader kill, output redaction | [codex.py:41-73](../../../src/vaani/codex.py#L41-L73) |
| Result surface | `ResultWindow` with an injected sink (`P0-05` still `partial`) | [codex.py:75-85](../../../src/vaani/codex.py#L75-L85) |
| Cancel | `Esc` / pill → recorder cleanup, or `codex.cancel()` during PROCESSING | [controller.py:143-160](../../../src/vaani/controller.py#L143-L160) |
| Platform contract | `PlatformBundle`: recorder, hotkeys, target, delivery, apps, browser, feedback, key_store, run | [platform/protocol.py:77-92](../../../src/vaani/platform/protocol.py#L77-L92) |
| History | One row per request; `cleanup_status` doubles as a route tag (`app_action`, `browser_action`) | [controller.py:267](../../../src/vaani/controller.py#L267) |

**Absent today, and required by nearly every use case below:** intent/slot types, a verb registry, a context resolver (workspace / focus / project), a confirmation engine, dry-run, undo, a per-OS capability matrix with explicit `unsupported`, managed child-process lifecycle, window/input/system adapter surfaces on `PlatformBundle`, and screen capture.

Two structural notes that shape the stack:

1. Routing lives inside `Controller._process`, interleaved with transcription, history, and state transitions. Every new verb added there multiplies that method. **Extracting the router is slice S0 and it is a prerequisite, not a nice-to-have.**
2. `cleanup_status` is being used as a routing label. The Result contract (§5.4) needs its own column set; don't grow more magic strings.

---

## 4. The escalation ladder

Adapted from OpenClicky's routing hierarchy. **Never skip to a higher rung when a lower rung matches.** Every result records the rung that served it.

| Rung | Name | Mechanism | LLM? | Latency target (post-transcript) | Example |
|---|---|---|---|---|---|
| **1** | Deterministic verb | Exact/alias grammar → registry handler | no | < 150 ms | "open Terminal", "mute" |
| **2** | Parameterized verb | Grammar + slot extraction & normalization | no | < 250 ms | "free port 3000", "set volume to 30 percent" |
| **3** | Project verb | Rung 2 + workspace/project profile lookup | no | < 600 ms | "run the tests", "start the dev server" |
| **4** | Integration verb | Typed adapter over a real API/CLI (`git`, `gh`, `docker`, browser CDP) | no | < 1.5 s | "create a branch called assistant use cases", "tail logs for the api container" |
| **5** | Guide / point | Screen capture → vision model → overlay pointer, no mutation | yes (vision) | < 3 s | "how do I turn off notifications here" |
| **6** | Agent task | Bounded runner (Codex / Claude / Cursor), may edit files & run commands | yes | seconds–minutes | "fix the failing test" |
| **7** | Computer-use | Synthetic input / accessibility-driven UI automation | yes | seconds–minutes | "click the Export button and save it to Downloads" |

### 4.1 Ladder rules

- **Fallback is downward-visible, never silent.** If rung 1–4 all miss, the user is told which rung will run and roughly what it costs before rung 6/7 starts (unless the wake phrase already opted in, §12.5).
- **Rung ≥ 6 always confirms** unless the wake phrase was used, and even then it materializes its plan first (§12.2).
- **Rung 7 never runs unconfirmed, ever**, and is disabled by default until its capability pack is installed.
- **A rung-1 phrase must never be shadowed by an LLM.** Regression test: the full rung-1/2 corpus must resolve with the brain adapter mocked to raise.
- **Rung is recorded** in history and in the result, so we can measure the ladder (what % of real usage stays ≤ rung 4? target: > 80%).

### 4.2 Conflict precedence within a rung

Ordered rules, first match wins:

1. Explicit surface qualifier: `website` / `web app` / `in brave` / `in chrome` → site verb, never app verb. (Already implemented; keep.)
2. Explicit app qualifier: `desktop`, `app` → app verb. "Open Claude" → app; "Open Claude website" → site.
3. Container/namespace qualifier: "kill the **api container**" → `container.stop`, not `proc.kill`.
4. Most specific verb wins: "git commit" → `vcs.commit`, not `terminal.run`.
5. User-local catalogs (`sites.json`, future `apps.json`, `commands.json`) shadow built-ins — user config always wins.
6. Two candidates still tied → **disambiguate, do not guess** (§12.3).

---

## 5. Contracts

These five types are the spine of the stack. Everything in §7–§11 is expressed against them.

### 5.1 Intent

```python
@dataclass(frozen=True)
class Intent:
    verb: str                       # "system.port.free"
    slots: Mapping[str, Any]        # {"port": 3000}
    rung: int                       # 1..7, which rung matched
    confidence: float               # 1.0 for grammar hits
    source: str                     # "grammar" | "llm" | "remote" | "cli"
    mode: str                       # "act" | "guide"
    utterance: str                  # normalized
    raw_utterance: str              # verbatim transcript
    modifiers: frozenset[str]       # {"dry_run", "force", "continue_session"}
    brain: str | None               # "codex" | "claude" | "cursor" — from "use X for this"
```

### 5.2 Context

Resolved once per request, before dispatch. Providers are lazy: a verb that doesn't declare `requires={"focus"}` never pays for a focus probe.

```python
@dataclass(frozen=True)
class Context:
    platform: PlatformId
    workspace: Path | None          # project root
    workspace_source: str           # "focused-editor" | "focused-terminal" | "config" | "last-used" | "home"
    repo: RepoInfo | None           # root, branch, dirty, upstream, remote host
    project: ProjectProfile | None  # §12.8 — package manager, scripts, test/build/dev commands
    focus: FocusInfo | None         # app id, window title, document path, selection
    screen: ScreenFrame | None      # only when the verb requires it, hotkey-gated
    session: AgentSession | None    # last agent session, for "continue"
```

Workspace resolution order (an **open decision** in the old doc, now proposed): focused editor's project → focused terminal's cwd → `VAANI_ASSISTANT_CWD` → last workspace used this boot → `$HOME`. The chosen source is always reported in the result, because "it did the right thing in the wrong repo" is the worst failure mode in this whole product.

### 5.3 Verb (the catalog entry)

hey-cli's lesson: the catalog is the product surface; voice is one caller.

```python
@dataclass(frozen=True)
class Verb:
    name: str                                  # "system.port.free"
    title: str                                 # "Free a TCP port"
    slots: Mapping[str, SlotSpec]
    rung: int
    risk: RiskClass
    requires: frozenset[str]                   # context providers: "workspace"|"repo"|"project"|"focus"|"screen"
    support: Mapping[PlatformId, Support]       # SUPPORTED | DEGRADED | UNSUPPORTED
    undo: str | None                           # inverse verb name, if any
    pack: str                                  # "core" | "git" | "docker" | "browser-cdp" | ...
    handler: Callable[[Intent, Context], Result]
```

Every verb is therefore reachable three ways, all sharing one code path:

```
vaani do system.port.free --port 3000 --json --dry-run     # CLI / tests / scripts
POST /v1/intent {"verb":"system.port.free","slots":{"port":3000}}   # remote (Phase 3)
"free port 3000"                                            # voice
```

…and a fourth: the agent brain at rung 6 is handed **the verb catalog as its tool list**, so "fix the failing test" runs `project.test.run` rather than inventing shell. That is the single highest-leverage safety decision in this document.

### 5.4 Result

```python
class Status(str, Enum):
    OK = "ok"; DRY_RUN = "dry_run"; NEEDS_CONFIRM = "needs_confirm"
    REFUSED = "refused"; UNSUPPORTED = "unsupported"; FAILED = "failed"; PARTIAL = "partial"

@dataclass(frozen=True)
class Result:
    status: Status
    summary: str                      # ≤ 80 chars — pill / notification line
    detail: str = ""                  # result window body
    evidence: tuple[str, ...] = ()    # argv actually run, exit codes, PIDs
    rung: int = 0
    undo: UndoToken | None = None
    pending: PendingAction | None = None   # set when NEEDS_CONFIRM
    overlay: tuple[OverlayOp, ...] = ()    # point/caption ops for guide mode
```

`summary` is written for a glance, not for narration. "Freed port 3000 (killed node, PID 41233)" — not a paragraph.

### 5.5 Risk classes

| Class | Policy | Members |
|---|---|---|
| **R0** silent | Run immediately, reversible, no data loss | open app/site, focus window, volume, mute, DND, reveal in file manager, clear terminal, `git status`-shaped reads |
| **R1** notify | Run immediately, report clearly what changed | `git pull`, install deps, start dev server, new branch, stash |
| **R2** confirm | One confirmation | kill process/port, quit app, empty trash, `git commit`, amend, discard *one* file, stop containers, Wi-Fi off |
| **R3** high friction | Explicit confirm + the materialized command shown; never auto-approvable by the brain | force push, discard all changes, `rm -rf node_modules`, merge PR, `terraform apply`, delete branch |
| **R4** blocked by default | Requires config opt-in per verb, then behaves as R3 | anything needing sudo (flush DNS on macOS), `export` from `.env`, arbitrary shell, rung 7 computer-use |

Rule: risk is a property of the **verb**, not of the phrasing. The user cannot talk a verb down a class; only config can.

---

## 6. The use-case definition template

A use case is defined when all 14 fields exist. Copy this block per use case.

```
UC-ID:            SYS-PORT-01
Utterances:       canonical + variants + must-NOT-match near misses
Verb & slots:     system.port.free {port:int}
Rung / mode:      2 / act
Context required: —
Preconditions:    port in 1..65535; process owned by user
Mechanism:        macOS · Windows · Linux (each: supported/degraded/unsupported + how)
Risk / confirm:   R2 — confirm names the process and PID
Success signal:   summary text; where it appears
Failure & degrade: per-cause message, incl. "can't do this on this OS"
Undo:             none | inverse verb
ASR pitfalls:     normalizations required
Verification:     unit / integration / manual
Depends on:       stack layers + feature IDs
```

Fields with no honest answer get the literal value `spike` plus a one-line question. `spike` is a valid state; a blank is not.

---

## 7. System use cases

OS-level state and resources. Success = machine state changed, or the right OS surface is in front of the user.

### 7.1 Worked example — free a port

```
UC-ID:            SYS-PORT-01
Utterances:       "free port 3000" · "kill whatever is on port 8080" · "what's on port 3000"(→guide)
                  · "who's using port 3000"(→guide) · "kill the thing on port three thousand"
  must NOT match: "open localhost 3000" (→ browser.open) · "forward port 3000" (unsupported, refuse)
Verb & slots:     system.port.free {port:int, signal:"term"|"kill"="term"}
Rung / mode:      2 / act (or guide when interrogative → find, report, offer)
Context required: —
Preconditions:    1 ≤ port ≤ 65535; holder PID owned by current user (else R4 sudo path, refused by default)
Mechanism:
  macOS    supported  lsof -nP -iTCP:<port> -sTCP:LISTEN -t  → SIGTERM, then SIGKILL after 2s
  Linux    supported  ss -lptnH 'sport = :<port>' (fallback lsof) → same escalation
  Windows  supported  Get-NetTCPConnection -LocalPort <port> -State Listen → Stop-Process -Id
Risk / confirm:   R2 — confirm text names process + PID: "Kill node (PID 41233) on port 3000?"
Success signal:   "Freed port 3000 — stopped node (41233)."  evidence = argv + exit codes
Failure & degrade: nothing listening → OK-with-nothing-done, "Port 3000 is already free."
                  holder owned by another user/root → REFUSED, "Port 3000 is held by root; Vaani won't sudo."
                  process survives SIGKILL → FAILED with PID, offer Activity Monitor / Task Manager
Undo:             none — killing is irreversible. This is why it is R2 and names the process.
ASR pitfalls:     "three thousand"→3000; "eighty eighty"→8080; "port three zero zero zero"→3000;
                  "localhost 3000" must not be read as a bare port
Verification:     unit: slot parse + number words + per-OS argv shape (fake runner)
                  integration: bind a real socket in a fixture, free it, assert released
                  manual: all three OSes, plus the "already free" and "root-owned" branches
Depends on:       L1 grammar+normalize · L3 procsup · L4 confirm · L5 process runner
```

### 7.2 System — remaining use cases

| UC-ID | Utterance | Verb | Rung | Risk | macOS | Windows | Linux | Notes |
|---|---|---|---|---|---|---|---|---|
| SYS-PROC-01 | "kill the process named node" | `system.proc.kill` | 2 | R2 | `pkill -f` | `Stop-Process -Name` | `pkill -f` | Confirm must list **all** matches and their count; refuse if > 5 unless `force` |
| SYS-PROC-02 | "show what's using the most CPU" | `system.proc.top` | 1 | R0 | open Activity Monitor | open Task Manager | open System Monitor | Act, don't narrate. Guide variant may point at the top row |
| SYS-PROC-03 | "restart the Vaani process" | `system.self.restart` | 2 | R2 | own supervisor | own supervisor | own supervisor | Must survive its own restart → out-of-process relaunch; see `docs/OPERATIONS.md` |
| SYS-DISK-01 | "empty the trash" | `system.trash.empty` | 1 | R2 | `osascript` Finder empty | `Clear-RecycleBin -Force` | `gio trash --empty` | Confirm states item count if cheaply available |
| SYS-DISK-02 | "show free disk space" | `system.storage.show` | 1 | R0 | Storage settings pane | Settings → Storage | `baobab` / Disk Usage | Open the surface; `guide` may point at the bar |
| SYS-DISK-03 | "eject the USB drive" | `system.volume.eject` | 2 | R2 | `diskutil eject` | `spike` — no clean CLI; shell COM `InvokeVerb("Eject")` | `udisksctl unmount` + `power-off` | Ambiguous when > 1 removable → disambiguate |
| SYS-FILE-01 | "zip this project folder" | `files.archive` | 3 | R1 | `ditto -c -k` | `Compress-Archive` | `zip -r` | Needs `workspace`; output path reported |
| SYS-FILE-02 | "move all screenshots from desktop into a Screenshots folder" | `files.sweep` | 2 | R2 | shared impl | shared impl | shared impl | Confirm shows N files; undo = inverse move (this one **is** undoable) |
| SYS-FILE-03 | "create a note called meeting notes on my desktop" | `files.create_note` | 2 | R1 | write file + open | write file + open | write file + open | Refuse overwrite; suffix instead |
| SYS-FILE-04 | "find my resume PDF" | `files.find` | 2 | R0 | `mdfind` | Windows Search / `Get-ChildItem` | `locate`/`plocate` fallback `find` | Success = **reveal**, not a spoken list. 0 hits → say so; >1 → disambiguate |
| SYS-FILE-05 | "reveal this project in Finder/Explorer/Files" | `files.reveal` | 3 | R0 | `open -R` | `explorer /select,` | `nautilus --select` / `xdg-open` | Needs `workspace`; report which workspace |
| SYS-NET-01 | "turn off Wi-Fi" | `system.wifi.set` | 2 | R2 | `networksetup -setairportpower <dev> off` (device lookup required) | `Disable-NetAdapter` — **admin**, so R4 | `nmcli radio wifi off` | Undoable → inverse verb. Confirm because it can kill your own session |
| SYS-NET-02 | "copy my local IP address" | `system.ip.copy` | 1 | R0 | `ipconfig getifaddr en0` w/ fallback | `Get-NetIPAddress` | `ip -4 addr` / `hostname -I` | Delivers to clipboard — the one `act` verb that writes the clipboard |
| SYS-NET-03 | "flush DNS" | `system.dns.flush` | 1 | R4 macOS / R2 else | needs sudo → **R4, off by default** | `ipconfig /flushdns` | `resolvectl flush-caches` | Best example of per-OS risk divergence |
| SYS-APP-01 | "open System Settings / Activity Monitor / Terminal" | `app.open` | 1 | R0 | `open -a` | shell name / AppUserModelId | `which` + exec | **Works today.** Catalogs need parity across the three OSes |
| SYS-APP-02 | "quit Slack" | `app.quit` | 1 | R2 | `osascript … quit` (graceful) | `CloseMainWindow` then `Stop-Process` | `wmctrl -c` then SIGTERM | Graceful first, always; never `-9` without a second confirm |
| SYS-AV-01 | "mute" / "unmute" / "set volume to 30 percent" | `system.volume.set` | 2 | R0 | `osascript set volume` | `spike` — **no built-in CLI**; needs Core Audio COM (pycaw) or a bundled helper | `pactl` / `wpctl` (PipeWire) | Windows is the real gap; the pack must declare `DEGRADED` until solved |
| SYS-AV-02 | "turn on Do Not Disturb" | `system.dnd.set` | 1 | R0 | `spike` — no supported API since Ventura; Shortcuts-run workaround | `spike` — Focus Assist has no stable API | `gsettings … show-banners false` | Likely `DEGRADED` on two of three OSes; say so out loud |
| SYS-PWR-01 | "lock the screen" | `system.lock` | 1 | R0 | `CGSession -suspend` | `rundll32 user32.dll,LockWorkStation` | `loginctl lock-session` | Clean three-OS parity — good first slice member |
| SYS-PWR-02 | "sleep the display" | `system.display.sleep` | 1 | R0 | `pmset displaysleepnow` | `SetSuspendState`/monitor-off | `xset dpms force off` | Wayland: `spike` |

---

## 8. Terminal use cases

The category with the most hidden design. "Terminal" splits into **three mechanically different families**, and conflating them is how this feature goes wrong.

| Family | What it means | Verifiable? | Mechanism |
|---|---|---|---|
| **T-A: Managed job** | Vaani owns the child process, its logs, and its lifecycle | yes — PID, exit code, log tail | Process supervisor (§12.9) |
| **T-B: Terminal app** | Open / focus a terminal window at a directory | partially — the window exists | App launcher with cwd argument |
| **T-C: Foreign shell mutation** | Change state *inside* a shell the user already has open | **no** | Synthetic keystrokes into the focused terminal (rung 7) |

**The decision that unblocks this whole category:** "start the dev server" is **T-A**, not T-C. Vaani spawns and owns it, so "stop the dev server", "restart it", and "why did it die" all become answerable. Typing into whatever terminal happened to be focused makes every one of those unanswerable.

### 8.1 Worked example — run the tests

```
UC-ID:            TERM-TEST-01
Utterances:       "run the tests" · "run the test suite" · "run the unit tests only"
                  · "watch the tests" · "run the tests for the controller"
  must NOT match: "fix the failing test" (→ agent.task, rung 6)
                  · "why are the tests failing" (→ guide over last run's log)
Verb & slots:     project.test.run {scope:"all"|"unit"|"integration"|None, filter:str|None, watch:bool=False}
Rung / mode:      3 / act
Context required: workspace, project
Preconditions:    ProjectProfile resolved a test command; no managed job already running for this key
Mechanism:        OS-agnostic — the supervisor spawns ProjectProfile.test argv in workspace.
                  Per-OS only in process-group creation & kill (already solved in codex.py:19-39).
                  Detection order: vaani.toml override → package.json scripts.test → pyproject
                  ([tool.pytest] / hatch / poe) → Makefile `test` → pytest if tests/ exists → refuse.
Risk / confirm:   R1 — runs immediately, reports the argv it chose
Success signal:   live: pill shows "pytest — running"; done: "Tests passed (128) in 41s"
                  or "12 failed / 128" + result window with the failing names
Failure & degrade: no test command found → REFUSED naming the files it looked at, and offering
                    to write `vaani.toml`. Never guess `npm test` in a Python repo.
                  ambiguous workspace → disambiguate, never pick silently
Undo:             none needed (read-only-ish); cancel = supervisor stop
ASR pitfalls:     "pie test"/"py test"→pytest · "NPM"→npm · "unit tests only"→scope=unit
                  · filter names need the custom-vocabulary lexicon (ROADMAP P2-09)
Verification:     unit: profile detection across ~10 fixture repo shapes; argv never shell-joined
                  integration: fake repo with a passing and a failing suite; assert summary + exit code
                  manual: three OSes; Ctrl+C-equivalent cancel mid-run
Depends on:       L2 workspace+project · L3 supervisor · L6 result window
```

### 8.2 Terminal — remaining use cases

| UC-ID | Utterance | Verb | Family | Rung | Risk | Notes |
|---|---|---|---|---|---|---|
| TERM-DEV-01 | "start the dev server" | `project.dev.start` | T-A | 3 | R1 | Detect from `scripts.dev`/`start`, `manage.py runserver`, `uvicorn`. Report the URL it detects from the log, and offer to open it |
| TERM-DEV-02 | "stop the dev server" | `project.dev.stop` | T-A | 3 | R1 | Needs the job registry. Also must handle "it's running but I didn't start it via Vaani" → fall back to port-based kill with R2 confirm |
| TERM-DEV-03 | "restart the dev server" | `project.dev.restart` | T-A | 3 | R1 | stop→start with the same argv; report if the argv changed since |
| TERM-DEV-04 | "install the dependencies" | `project.deps.install` | T-A | 3 | R1 | Package manager from lockfile, not from speech: `pnpm-lock` → pnpm, `uv.lock` → uv |
| TERM-DEV-05 | "build the project" | `project.build` | T-A | 3 | R1 | Long-running; needs progress in the pill, not a frozen spinner |
| TERM-DEV-06 | "typecheck the project" | `project.typecheck` | T-A | 3 | R1 | `tsc --noEmit`, `mypy`, `pyright` from profile |
| TERM-JOB-01 | "what's running" / "show the dev server logs" | `job.list` / `job.logs` | T-A | 1 | R0 | The supervisor's own surface. Success = result window with a log tail |
| TERM-SESS-01 | "open a terminal in this project" | `app.terminal.open` | T-B | 3 | R0 | macOS `open -a Terminal <dir>` (iTerm variant); Windows `wt -d <dir>`; Linux `gnome-terminal --working-directory=` |
| TERM-SESS-02 | "go to the Vaani project folder in the terminal" | `terminal.cd` | T-C | 7 | R2 | Keystroke injection into the focused terminal. **Unverifiable** — result must say "typed, not verified" |
| TERM-SESS-03 | "clear the terminal" | `terminal.clear` | T-C | 7 | R0 | Send `Ctrl+L`. Cheap, safe, and honest about being blind |
| TERM-SESS-04 | "run the last command again" | `terminal.repeat` | T-C | 7 | R3 | **Dangerous by construction** — Vaani cannot know what the last command was. Materialize nothing → so: refuse by default, or send `!!`+Enter only under an opt-in pack |
| TERM-ENV-01 | "activate the virtual environment" | `terminal.venv.activate` | T-C | 7 | R2 | Cannot mutate a foreign shell's env from outside. Keystrokes only. Document the limit in the UI, don't pretend |
| TERM-ENV-02 | "export the env from .env" | `terminal.env.export` | T-C | 7 | **R4** | Secrets into a shell Vaani can't see. Off by default, per-verb opt-in, never logged |
| TERM-ENV-03 | "use Python 3.12 in this shell" | `terminal.python.use` | T-C | 7 | R2 | Same limit as ENV-01; `pyenv`/`uv` keystrokes |
| TERM-DRY-01 | "don't run it — just show me the command" | *(modifier)* | — | — | — | Not a verb. Sets `dry_run` on the next/current intent (§12.2) |

**Family-level honesty requirement:** every T-C result carries the literal caveat `typed into the focused terminal; outcome not verified` in `detail`. If we can't verify it, we don't claim it.

---

## 9. CLI use cases

Orders aimed at a named developer tool. These are rung 4 **integration verbs**: typed adapters with argv arrays, never shell strings, never speech interpolated into a command line.

### 9.1 Worked example — create a branch

```
UC-ID:            CLI-GIT-BRANCH-01
Utterances:       "create a new branch called assistant use cases"
                  · "make a branch for the linux fix" · "new branch feature slash port killer"
  must NOT match: "switch to main" (→ vcs.checkout) · "delete the branch" (→ vcs.branch.delete, R3)
Verb & slots:     vcs.branch.create {name:str, from_ref:str|None, checkout:bool=True}
Rung / mode:      4 / act
Context required: repo
Preconditions:    inside a git work tree; name valid per git-check-ref-format; not already existing
Mechanism:        all OSes — argv ["git","-C",<repo.root>,"switch","-c",<name>] (fallback checkout -b).
                  Identical on three OSes; only git discovery differs.
Risk / confirm:   R1 — but the **slugified name is shown before creation**, because the transcript
                  is the name. This is heyCLI's materialize-then-run applied to a slot, not a command.
Success signal:   "Created and switched to assistant-use-cases (from main)."
Failure & degrade: not a repo → REFUSED with the workspace it checked
                  · name exists → offer checkout instead
                  · dirty tree blocking switch → report files, offer stash (separate confirm)
Undo:             yes — `vcs.branch.delete` of a just-created branch + checkout previous ref
ASR pitfalls:     **slugification is the whole use case**: "assistant use cases"→assistant-use-cases
                  · "feature slash port killer"→feature/port-killer · "underscore"/"dash"/"slash" as
                  literal separators · casing normalized to lower-kebab unless the repo's existing
                  branch names indicate another convention (read `git branch` to infer)
Verification:     unit: slug table (~30 cases) + ref-format rejection + argv shape
                  integration: temp repo — create, collision, dirty tree
                  manual: repo whose convention is snake_case; confirm inference
Depends on:       L1 normalize+slug · L2 repo context · L4 confirm+undo · L5 runner
```

### 9.2 CLI — remaining use cases

Git (pack `git`):

| UC-ID | Utterance | Verb | Risk | Notes |
|---|---|---|---|---|
| CLI-GIT-02 | "switch to main" | `vcs.checkout` | R1→R2 if dirty | Dirty tree turns this into a confirm with a stash offer |
| CLI-GIT-03 | "pull latest" | `vcs.pull` | R1 | `--ff-only` by default; a merge/rebase need is reported, not silently resolved |
| CLI-GIT-04 | "commit my changes with a reasonable message" | `vcs.commit` | R2 | Message generated by the brain from the diff → **must be shown verbatim before committing**. This is the flagship materialize-then-confirm case |
| CLI-GIT-05 | "amend the last commit message" | `vcs.commit.amend` | R2 | Refuse if the commit is already pushed unless `force` |
| CLI-GIT-06 | "push this branch" | `vcs.push` | R1 | `--set-upstream` when absent; report the remote |
| CLI-GIT-07 | "force push" | `vcs.push.force` | **R3** | `--force-with-lease` only. Never `--force`. Confirm shows the exact argv |
| CLI-GIT-08 | "discard the changes in this file" | `vcs.restore` | **R3** | Needs `focus.document_path`. Irreversible → shows the file and the line delta before acting |
| CLI-GIT-09 | "stash my changes" / "pop the stash" | `vcs.stash.push/pop` | R1 | Undo pair for each other |
| CLI-GIT-10 | "show the diff" | `vcs.diff.show` | R0 | Act: open in editor/`gh`/difftool. Not a spoken walkthrough |
| CLI-GH-01 | "create a pull request" | `forge.pr.create` | R2 | `gh pr create --fill`; confirm shows title + base. Requires `gh` auth → clear degrade message |
| CLI-GH-02 | "open the PR I was working on" | `forge.pr.open` | R0 | `gh pr view --web`; ambiguity → disambiguate |
| CLI-GH-03 | "check out the PR branch for pull request 3" | `forge.pr.checkout` | R1 | Number slot; "PR three" → 3 |
| CLI-GH-04 | "merge the pull request" | `forge.pr.merge` | **R3** | Shows repo, number, title, and merge strategy before acting |

Package managers (pack `pkg`):

| UC-ID | Utterance | Verb | Risk | Notes |
|---|---|---|---|---|
| CLI-PKG-01 | "install lodash" / "add requests to the project" | `pkg.add` | R2 | Manager from lockfile. Confirm shows the package name **as heard** — typosquat risk makes this R2, not R1 |
| CLI-PKG-02 | "update the lockfile" | `pkg.lock` | R1 | |
| CLI-PKG-03 | "run npm run lint" | `pkg.script.run` | R1 | Script name must exist in the manifest; unknown → list what does exist |
| CLI-PKG-04 | "remove node_modules and reinstall" | `pkg.reinstall` | **R3** | Deletes a directory. Path must be inside `workspace` — hard guard, tested |

Containers (pack `docker`):

| UC-ID | Utterance | Verb | Risk | Notes |
|---|---|---|---|---|
| CLI-DOCK-01 | "start docker" | `app.open` (Docker Desktop) / `container.engine.start` | R1 | macOS/Windows = launch the app and wait for the daemon; Linux = `systemctl --user start docker` |
| CLI-DOCK-02 | "list running containers" | `container.list` | R0 | Result window table, or open Docker Desktop |
| CLI-DOCK-03 | "stop all containers" | `container.stop_all` | **R3** | Confirm lists names and count |
| CLI-DOCK-04 | "rebuild the compose stack" | `container.compose.rebuild` | R2 | Needs `workspace` + a compose file; long-running → supervisor job (T-A) |
| CLI-DOCK-05 | "tail logs for the api container" | `container.logs` | R0 | Service-name slot resolved against the compose file, so mishears become a disambiguation, not a miss |

Other (pack `sys-pkg`, `iac`):

| UC-ID | Utterance | Verb | Risk | Notes |
|---|---|---|---|---|
| CLI-SYS-01 | "brew update" / "apt update" | `syspkg.update` | R2 / **R4** on apt (sudo) | Homebrew needs no sudo; apt does → different risk on different OSes, same verb |
| CLI-AUTH-01 | "login to gh" | `forge.auth.login` | R1 | Interactive: Vaani's job is to hand off to a real terminal, then get out of the way |
| CLI-IAC-01 | "run terraform plan" | `iac.plan` | R2 | Plan is safe-ish; **`iac.apply` is a separate R3 verb and is never reachable by fallback from "plan"** |

**Category-wide rules**

1. Slots that came from speech and end up in a command are shown before execution when the verb is R2+ (branch names, package names, commit messages, service names).
2. Never `shell=True`. Never string-join argv. Grep-able invariant, enforced by a lint test.
3. Every pack declares its binary and auth preconditions, so a missing `gh`/`docker` produces "gh isn't installed" rather than a rung-6 escalation that tries to install it.

---

## 10. UI use cases

Graphical apps, windows, and in-app navigation. Same *command* everywhere; wildly different *how*. This category has the widest OS variance and the largest permission surface.

### 10.1 Worked example — open this folder in Cursor

```
UC-ID:            UI-EDIT-01
Utterances:       "open this folder in Cursor" · "open my Vaani project in Cursor"
                  · "open this in VS Code" · "open the file with the build error"(→UI-EDIT-04)
  must NOT match: "open Cursor" (→ app.open, rung 1, no path)
Verb & slots:     editor.open {editor:"cursor"|"code"|"zed"|…, path:Path, line:int|None}
Rung / mode:      3 / act
Context required: workspace (for "this"), or a named-project lookup
Preconditions:    editor CLI or bundle present; path exists
Mechanism:        macOS   `cursor <path>` / `open -a Cursor <path>`; `code -g file:line`
                  Windows `cursor.cmd <path>` / Start-Process; same `-g` for code
                  Linux   `cursor <path>` via which; flatpak variant is a spike
Risk / confirm:   R0
Success signal:   "Opened /Users/…/Vaani in Cursor." — the **resolved path is always shown**,
                  because "opened the wrong project" is silent otherwise
Failure & degrade: editor missing → offer the other installed editor rather than failing flat
                  · "this" unresolvable → disambiguate from recent workspaces
                  · named project unknown → refuse, offer to register it
Undo:             none (opening is harmless)
ASR pitfalls:     "cursor"/"curser"/"the cursor" · "VS Code"/"vs code"/"vise code"
                  · project names need the vocabulary lexicon
Verification:     unit: editor resolution order, argv, line-number form
                  integration: fake editor binary asserts argv
                  manual: three OSes, plus "this" with a terminal focused vs an editor focused
Depends on:       L2 workspace+focus · L5 runner
```

### 10.2 UI — remaining use cases

Browser (pack `core`, optional pack `browser-cdp`):

| UC-ID | Utterance | Verb | Rung | Risk | Notes |
|---|---|---|---|---|---|
| UI-BROW-01 | "open Gmail" / "go to Stripe dashboard" | `site.open` | 1 | R0 | **Works today** via `sites.json` + built-ins |
| UI-BROW-02 | "open localhost 3000" | `site.open` {url} | 2 | R0 | Number normalization; prefer the port a managed job reported |
| UI-BROW-03 | "search Google for pulseaudio mute microphone" | `site.search` | 2 | R0 | Query is free text → URL-encode; the one place raw speech legitimately flows through |
| UI-BROW-04 | "open ChatGPT and start a new chat" | `site.open` {url with intent} | 1 | R0 | Solved by picking the right URL (`/new`), not by automation. Already the pattern in `sites.py` |
| UI-BROW-05 | "refresh this tab" / "close this tab" | `browser.tab.reload/close` | 7 | R0/R2 | **Keystroke macro** (Cmd/Ctrl+R, Cmd/Ctrl+W) with a focus precondition — not browser control. Close is R2: it can destroy a form |
| UI-BROW-06 | "open a new incognito window" | `browser.window.private` | 1 | R0 | Real CLI flags exist (`--incognito`, `--inprivate`) → stays rung 1 |
| UI-BROW-07 | "which tab is my staging site" | `browser.tab.find` | 4 | R0 | Needs the `browser-cdp` pack (`--remote-debugging-port`) or an extension. Without it: `UNSUPPORTED`, say so |

Editor / IDE:

| UC-ID | Utterance | Verb | Rung | Risk | Notes |
|---|---|---|---|---|---|
| UI-EDIT-02 | "format this file" | `editor.format` | 7 | R1 | Keystroke macro, or rung 4 via the project formatter (`prettier`/`ruff`) — **prefer rung 4**, it's verifiable |
| UI-EDIT-03 | "go to definition" / "find references" | `editor.nav` | 7 | R0 | Pure keystroke macro; focus precondition; unverifiable |
| UI-EDIT-04 | "open the file with the build error" | `editor.open` {from last job} | 3 | R0 | Beautiful supervisor payoff: parse the last managed job's log for `file:line` |
| UI-EDIT-05 | "add logging to this function" / "refactor this file" | `agent.task` | 6 | R2 | Needs `focus.document_path` + selection. Diff is shown before it's applied |
| UI-EDIT-06 | "fix the failing test" | `agent.task` | 6 | R2 | Seeded with the last test job's failure output — that context is what makes it work |

Windows:

| UC-ID | Utterance | Verb | Rung | Risk | Notes |
|---|---|---|---|---|---|
| UI-WIN-01 | "focus Chrome" / "focus the terminal" | `window.focus` | 4 | R0 | macOS `activate` (Accessibility); Windows `SetForegroundWindow` + foreground-lock caveats; X11 `wmctrl -a`; **Wayland: UNSUPPORTED** |
| UI-WIN-02 | "split the window left" | `window.tile` | 4 | R0 | macOS 15 tiling / Windows Snap / GNOME keybinding. Three different mechanisms, one verb |
| UI-WIN-03 | "hide all other windows" / "show desktop" | `window.hide_others` | 4 | R0 | macOS `Cmd+Opt+H`; Windows `Win+D`; GNOME `Super+D` |

File manager & apps:

| UC-ID | Utterance | Verb | Rung | Risk | Notes |
|---|---|---|---|---|---|
| UI-FM-01 | "open Downloads" / "open Desktop" | `files.open_dir` | 1 | R0 | Known-folder resolution per OS, not hardcoded `~/Downloads` |
| UI-FM-02 | "new folder on the desktop named screenshots" | `files.mkdir` | 2 | R1 | Name slugified; collision → suffix, never overwrite |
| UI-APP-01 | "open Slack / Notes / Calendar" | `app.open` | 1 | R0 | Catalog parity across three OSes is the work |
| UI-APP-02 | "draft a reply saying I'll send it by Friday" | `comms.draft` | 4/6 | R2 | Rung 4 where an API exists (`mailto:`, app URL scheme); rung 6/7 otherwise. **Never sends** — draft only, always |
| UI-APP-03 | "remind me in an hour to stretch" | `reminder.create` | 4 | R1 | macOS Reminders via `osascript`; Windows Task Scheduler / Notification; Linux `at`/`notify-send` + timer. Relative-time slot parsing |

**Permission preconditions to surface once, at install, not per-command:** macOS Accessibility + Input Monitoring + Screen Recording; Windows UIAccess & foreground-lock rules; Linux X11 vs Wayland capability split (Wayland turns rung 7 and `window.*` largely `UNSUPPORTED` — the honest answer, per ROADMAP `P0-06`).

---

## 11. Guide use cases (new, from Clicky)

The rung-5 mode the old doc was missing. Success = **a pointer on the user's screen plus one line of speech or text**. Never a lecture, never a mutation.

### 11.1 Worked example — point at a setting

```
UC-ID:            GUIDE-01
Utterances:       "how do I turn off notifications here" · "where's the export button"
                  · "what do I click to share this"
Verb & slots:     guide.point {question:str}
Rung / mode:      5 / guide
Context required: screen (captured on the same hotkey press), focus
Preconditions:    screen-capture permission granted; guide pack enabled
Mechanism:        capture the focused display → vision model → overlay ops.
                  Overlay protocol (OpenClicky's, adopted): point(x,y,label), caption(x,y,text),
                  tour([...ops]). Coordinates are display-relative and validated against
                  the captured frame's bounds before rendering.
                  macOS ScreenCaptureKit · Windows Graphics.Capture · Linux X11 shm / Wayland portal
Risk / confirm:   R0 — pointing mutates nothing. Capture itself is the sensitive act (see below)
Success signal:   an animated pointer at the target + ≤ 80-char caption. No system cursor warping
Failure & degrade: model can't find the element → say so and offer a search verb.
                  Capture unsupported (Wayland w/o portal) → UNSUPPORTED with the reason
Undo:             overlay auto-dismisses (timeout + any keypress)
ASR pitfalls:     "here"/"this" resolve to the focused window, not the whole desktop
Privacy:          frame held in memory only, never written to disk, never in history;
                  history records the question and the verb, not the pixels.
                  Capture happens only while the hotkey is held.
Verification:     unit: coordinate validation + overlay op serialization + bounds rejection
                  integration: fixed screenshot fixture → deterministic mocked ops → assert overlay
                  manual: multi-display, scaled displays (Retina/125%/fractional), three OSes
Depends on:       L2 screen provider · L6 overlay bus · brain adapter with vision
```

### 11.2 Guide — remaining use cases

| UC-ID | Utterance | Verb | Notes |
|---|---|---|---|
| GUIDE-02 | "how do I free port 3000" | `guide.offer` | Find the holder, state it, **offer** the `act` verb. One utterance → finding + offer + confirm |
| GUIDE-03 | "why did the build fail" | `guide.explain_job` | Reads the last managed job's log — no screen needed, so it's cheap and deterministic to fetch |
| GUIDE-04 | "walk me through connecting this to Stripe" | `guide.tour` | Multi-marker recordable tour (OpenClicky's `screen-tour` skill). Later slice |
| GUIDE-05 | "what can you do" | `guide.capabilities` | Answer by **opening the verb catalog UI**, not by narrating. Filtered to this OS's supported verbs |
| GUIDE-06 | "what did you just do" | `guide.last_result` | Reopens the last result with its evidence (argv, exit codes, rung) |

**Why guide mode belongs in this document:** it makes the mutating verbs safe to ship. Every interrogative that would otherwise be a misfire becomes a finding plus an offer. GUIDE-02 is the safety valve for all of §7.

---

## 12. Cross-cutting subsystems

Each one is a stack layer, and each is required by many use cases above.

### 12.1 Normalization & lexicon (L1)

- Number words → digits (`three thousand`→3000, `eighty eighty`→8080, `thirty percent`→30).
- Separator words → characters (`slash`, `dash`, `underscore`, `dot`).
- Slugification with per-repo convention inference (CLI-GIT-BRANCH-01).
- Tech lexicon biasing: `pytest`, `npm`, `pnpm`, `uv`, `gh`, `kubectl`, `nginx` + user vocabulary (ROADMAP `P2-09`). Also project and container names, harvested from context so mishears become disambiguation instead of misses.
- Filler stripping (`umm`, `please`, `can you`) before grammar matching, never before storing the raw transcript.

### 12.2 Confirmation, dry-run, materialization (L4)

- `PendingAction{id, verb, slots, materialized_argv, expires_at}` — single-use, TTL ~20 s, invalidated by any new utterance or a state change to the target.
- Three confirm paths, all required (A9): press the assistant hotkey again, say "confirm"/"yes"/"do it", or click the pill. Hands may be nowhere near the keyboard.
- R3 confirms display the exact argv. R4 verbs additionally require config opt-in and refuse with a pointer to the setting.
- `dry_run` is a modifier on **every** verb ("don't run it, just show me the command"). Output: the argv + resolved context + risk class + what would change. This is heyCLI's product as one flag on our product.
- The brain at rung 6 can never self-approve an R3/R4 verb. Enforced in the registry, tested.

### 12.3 Disambiguation (L4)

Two candidates, low confidence, or multiple matched targets → present at most 3 options in the pill, selectable by voice ("the first one") or hotkey. Timeout → cancel, never a default pick. Applies to: multiple removable drives, several `node` processes, ambiguous workspace, several matching PRs, unknown project name.

### 12.4 Capability matrix & degradation (L3)

Every verb declares `SUPPORTED | DEGRADED | UNSUPPORTED` per OS with a reason string. Requirements:

- `UNSUPPORTED` produces a spoken/visible "can't do this on this OS, because X" — **never** a silent no-op, never a rung-6 escalation to work around a platform gap.
- `vaani caps --json` prints the matrix. It is generated from the registry, so it can't drift from reality, and it's what `guide.capabilities` (GUIDE-05) renders.
- Known `DEGRADED`/`spike` cells already identified above: Windows volume control, DND on macOS + Windows, USB eject on Windows, everything window/input-related on Wayland, `flush DNS` on macOS (sudo).

### 12.5 Rung-6 opt-in and cost surfacing

Wake phrase, borrowed from HeyClicky's "clicky agent": **"Vaani, agent: <task>"** (also "use Claude for this" / "use Codex for this" / "use Cursor for this", which additionally pick the brain) forces rung 6 and skips the router. Without the phrase, a rung-6 fallback tells the user first. History records rung + brain + wall time so the expensive path is measurable.

### 12.6 Agent session continuity (L5)

Per OpenClicky's continue-vs-spawn split: keep the last agent session for ~10 minutes. "and now also push it" continues; a topic change spawns. Today's `CodexRunner` is one-shot with a 30 s timeout ([codex.py:42](../../../src/vaani/codex.py#L42)) — session support and a longer, progress-reporting timeout are part of the agent-runner slice (ROADMAP `P2-06`).

### 12.7 Capability packs (L3)

`core` (apps, sites, system, files, window) ships on. `git`, `docker`, `pkg`, `forge`, `iac`, `browser-cdp`, `guide`, `computer-use` are installable and individually toggleable, each declaring its binaries, auth, and permissions. Rationale: hey-cli's `skill install` and OpenClicky's bundled-skills layout both converge on this, and it keeps the default attack surface small.

### 12.8 Project profile (L2)

`ProjectProfile{root, manager, test, build, dev, typecheck, lint, compose_file, env_files}` — detected from lockfiles/manifests/Makefile, cached per root with mtime invalidation, overridable by `vaani.toml` in the repo. Detection order is explicit and reported, and a miss **refuses with the list of files it checked** rather than guessing. Feeds all of §8 and several of §9.

### 12.9 Process supervisor (L3)

Owns T-A jobs: spawn detached in a process group, capture stdout/stderr to a bounded log, register `{key, argv, pid, started_at, log_path, port?}`, and expose start/stop/restart/status/logs. Group-kill semantics already exist and are cross-platform in [codex.py:19-39](../../../src/vaani/codex.py#L19-L39) — lift that into the supervisor rather than rewriting it. Jobs survive a Vaani restart via an on-disk registry, and adopt-or-refuse when a port is held by an unmanaged process.

### 12.10 Undo (L4)

Bounded stack (~10) of `UndoToken{verb, inverse_verb, slots, expires_at}`. Three classes: **reversible** (volume, DND, Wi-Fi, branch switch, folder create, file sweep), **compensatable** (stash↔pop, branch create↔delete), **irreversible** (kill, empty trash, force push, discard) — which is precisely why those are R2/R3. "Undo that" on an irreversible action says so plainly instead of half-trying.

### 12.11 Audit & history (L7)

Extend history beyond today's single row: `verb`, `slots` (redacted), `rung`, `risk`, `status`, `confirmed_by`, `evidence`, `duration_ms`, `workspace`, `workspace_source`, `brain`. Retire the `cleanup_status = "app_action"` convention. Remote-originated intents (Phase 3) log their caller. Secrets never land here — `_redact` already exists ([codex.py:15-17](../../../src/vaani/codex.py#L15-L17)) and belongs at the audit boundary too.

### 12.12 Latency budget

Hotkey release → visible effect: **rung 1–2 under 1 s total** including transcription (so the router must not touch the network); rung 3–4 under 2 s; rung 5 under 3 s with a "looking" state; rung 6+ shows progress within 1 s and streams thereafter. A verb that can't meet its budget must show a state, not a freeze.

---

## 13. The feature stack

### 13.1 Layers

```
L8  Input surfaces      voice · vaani do CLI · POST /v1/intent · agent tool-calls
L7  Audit & history     verb-level rows, redaction, remote attribution
L6  Output bus          pill · result window · overlay (point/caption/tour) · notify · speak
L5  Execution           argv runner · supervisor · agent runner (sessions) · input synthesis
L4  Policy              risk · confirm · dry-run · disambiguation · undo · packs
L3  Verb registry       catalog + handlers + capability matrix + packs
L2  Context             workspace · repo · project profile · focus · screen
L1  Understanding       normalize · lexicon · grammar (fast) · LLM parse (fallback) · ladder router
L0  Session (exists)    hotkeys · recorder · transcription · cancel · state machine
```

### 13.2 Modules (proposed paths)

```
src/vaani/intent/     schema.py normalize.py lexicon.py grammar.py llm.py router.py
src/vaani/context/    workspace.py repo.py project.py focus.py screen.py
src/vaani/verbs/      registry.py packs/{core,git,pkg,docker,forge,iac,browser,guide,computeruse}.py
src/vaani/policy/     risk.py confirm.py dryrun.py disambiguate.py undo.py audit.py
src/vaani/exec/       runner.py supervisor.py agent.py input.py
src/vaani/surface/    pill.py result.py overlay.py bridge.py   # bridge = Phase 3 + output primitives
src/vaani/platform/<os>/  + system.py window.py input.py terminal.py screen.py   # new bundle surfaces
```

`PlatformBundle` grows five surfaces (`system`, `window`, `input`, `terminal`, `screen`), each optional and each contributing to the capability matrix by its absence.

### 13.3 Feature IDs

Proposed as Phase-2 additions; existing ROADMAP IDs referenced where they already cover a row.

| ID | Feature | Layer | Depends on | Maps to |
|---|---|---|---|---|
| A-01 | Intent/Context/Verb/Result contracts | L1/L3 | — | new |
| A-02 | Extract routing out of `Controller._process` into the ladder router | L1 | A-01 | `P2-01` |
| A-03 | Normalization + lexicon + slugification | L1 | A-01 | `P2-09` |
| A-04 | Verb registry + capability matrix + `vaani caps` | L3 | A-01 | new |
| A-05 | `vaani do` CLI with `--json` / `--dry-run` / `--yes` | L8 | A-04 | new |
| A-06 | Confirmation engine (3 paths) + PendingAction | L4 | A-04 | `P2-05` |
| A-07 | Dry-run modifier + materialization | L4 | A-04 | new |
| A-08 | Workspace + repo + focus context providers | L2 | A-01 | `P2-07` |
| A-09 | Project profile detection + `vaani.toml` | L2 | A-08 | new |
| A-10 | Process supervisor + job registry | L5 | A-04 | `P2-04` |
| A-11 | Undo stack | L4 | A-06 | new |
| A-12 | Capability packs (install/toggle) | L3/L4 | A-04 | `P2-02` |
| A-13 | Result window + pill states + notify parity | L6 | A-01 | `P0-05` |
| A-14 | Overlay bus (point/caption/tour) | L6 | A-13 | new |
| A-15 | Screen capture provider (hotkey-gated) | L2 | A-08 | new |
| A-16 | Guide mode (rung 5) | L1/L5 | A-14, A-15 | new |
| A-17 | Agent runner v2: sessions, catalog-as-tools, progress | L5 | A-04, A-06 | `P2-06` |
| A-18 | Input synthesis (rung 7) + per-OS gating | L5 | A-06, A-12 | new |
| A-19 | Verb-level audit schema + history migration | L7 | A-04 | `P2-08` |
| A-20 | Bridge: `/v1/intent` + output primitives + `/events` | L8 | A-04, A-14 | `P3-01` |

### 13.4 Build slices

Each slice is demoable on all three OSes or explicitly degrades.

| Slice | Contents | Delivers | Gate to move on |
|---|---|---|---|
| **S0** Spine | A-01, A-02, A-04, A-05 | No new user-visible verbs — today's app/site/codex behavior re-expressed as three registry verbs behind the router | Existing tests green; `vaani do app.open --name Terminal --json` works; `Controller._process` has no routing logic left |
| **S1** Safe system | A-03, A-13 + SYS-APP-01/02, SYS-PWR-01, SYS-AV-01, SYS-FILE-05, UI-FM-01, UI-BROW-01/02/06 | The everyday R0 set | Capability matrix shows honest `DEGRADED` for Windows volume; all three OSes pass |
| **S2** Confirm & ports | A-06, A-07, A-11 + SYS-PORT-01, SYS-PROC-01/02, SYS-DISK-01, GUIDE-02 | First mutating verbs, with the confirm engine proven | Confirm works by voice, hotkey, and click; dry-run on every S1+S2 verb |
| **S3** Project & jobs | A-08, A-09, A-10 + TERM-TEST-01, TERM-DEV-01/02/03/04, TERM-JOB-01, UI-EDIT-01/04 | The developer core; T-A family established | Dev server + tests start/stop/restart on three OSes; profile refuses instead of guessing |
| **S4** Git & forge | A-12 + CLI-GIT-01…10, CLI-GH-01…04 | Materialize-then-confirm proven on generated content (commit messages, branch slugs) | R3 verbs unreachable without the exact argv shown; packs toggleable |
| **S5** Windows & focus | A-18 (gated) + UI-WIN-01/02/03, UI-BROW-05, UI-EDIT-02/03, TERM-SESS-01/02/03 | Focus and keystroke macros, with T-C honesty | Wayland reports `UNSUPPORTED` cleanly; every T-C result carries the "not verified" caveat |
| **S6** Guide | A-14, A-15, A-16 + GUIDE-01…03, 05, 06 | Screen-aware pointing; interrogatives stop being misfires | No frame ever written to disk; multi-display and scaled displays correct |
| **S7** Agent | A-17 + UI-EDIT-05/06, TERM/CLI rung-6 fallbacks, §12.5 wake phrase | Bounded delegation with the verb catalog as its tool list | Brain cannot self-approve R3/R4; sessions continue; progress visible |
| **S8** Remote | A-19, A-20 | Phone/remote hitting the same registry and the same policy | Remote R2+ enforces the same confirm; audit attributes the caller |

Slice ordering rationale: S0 stops the controller from accreting more inline routing; S2 proves the confirmation engine on a verb whose blast radius is one process; S3 unlocks the largest daily-value cluster; guide (S6) lands before agent (S7) because it converts misfires into offers, which makes rung 6 safer to expose.

### 13.5 Cross-cutting invariants (test these, every slice)

1. No `shell=True`, no string-joined argv, anywhere.
2. Every rung-1/2 phrase resolves with the brain adapter mocked to raise.
3. Every verb declares support for all three platforms; a missing cell fails the test suite.
4. No R2+ verb executes without a `PendingAction` that was created and consumed.
5. `UNSUPPORTED` never escalates to a higher rung as a workaround.
6. Nothing writes a screen frame to disk; nothing writes a secret to history.
7. Every result names its workspace and its rung.

---

## 14. Verification strategy

| Layer | Unit | Integration | Manual |
|---|---|---|---|
| L1 | Utterance corpus → expected `Intent`, including the must-NOT-match rows from §7–§11 | Grammar vs LLM-parse agreement on the corpus | Accent/speed variance, Hinglish mixing |
| L2 | Fixture repo shapes → expected `ProjectProfile`; workspace precedence table | Real repo + focused editor/terminal | Wrong-workspace scenarios (the worst failure mode) |
| L3 | Registry completeness; capability matrix has no blanks | `vaani do` for every verb against fakes | — |
| L4 | Risk mapping; PendingAction TTL/single-use; undo inverses | Confirm via all three paths | Hands-free confirm with the keyboard out of reach |
| L5 | argv shape per OS with fake runners; group-kill | Supervisor lifecycle incl. Vaani restart | Long jobs, cancel mid-run |
| L6 | Overlay op serialization; coordinate bounds | Fixture screenshot → deterministic ops | Multi-display, fractional scaling |
| L7 | Redaction; schema | Migration from today's rows | — |
| L8 | Request → Intent parity across voice/CLI/HTTP | Same verb via all four callers → identical Result | Remote from a phone |

**The corpus is an artifact.** Appendix A becomes a fixture file (`tests/data/utterances.yaml`) with expected verb, slots, rung, and risk per row, plus the negative cases. Regression protection for this whole document.

---

## Appendix A — command corpus → verb map

Every utterance from the original scope doc, mapped. `R` = risk class.

| Utterance | Verb | Rung | R | UC |
|---|---|---|---|---|
| Free port 3000 / kill whatever is on port 8080 | `system.port.free` | 2 | R2 | SYS-PORT-01 |
| Kill the process named node | `system.proc.kill` | 2 | R2 | SYS-PROC-01 |
| Show what's using the most CPU | `system.proc.top` | 1 | R0 | SYS-PROC-02 |
| Restart the Vaani process | `system.self.restart` | 2 | R2 | SYS-PROC-03 |
| Empty the trash | `system.trash.empty` | 1 | R2 | SYS-DISK-01 |
| Show free disk space | `system.storage.show` | 1 | R0 | SYS-DISK-02 |
| Eject the USB drive | `system.volume.eject` | 2 | R2 | SYS-DISK-03 |
| Zip this project folder | `files.archive` | 3 | R1 | SYS-FILE-01 |
| Move all screenshots from desktop… | `files.sweep` | 2 | R2 | SYS-FILE-02 |
| Create a note called meeting notes… | `files.create_note` | 2 | R1 | SYS-FILE-03 |
| Find my resume PDF | `files.find` | 2 | R0 | SYS-FILE-04 |
| Reveal this project in Finder/Explorer/file manager | `files.reveal` | 3 | R0 | SYS-FILE-05 |
| Turn on/off Wi-Fi | `system.wifi.set` | 2 | R2/R4 | SYS-NET-01 |
| Copy my local IP address | `system.ip.copy` | 1 | R0 | SYS-NET-02 |
| Flush DNS | `system.dns.flush` | 1 | R2/R4 | SYS-NET-03 |
| Open System Settings / Activity Monitor / Task Manager / Terminal | `app.open` | 1 | R0 | SYS-APP-01 |
| Quit Slack | `app.quit` | 1 | R2 | SYS-APP-02 |
| Mute / unmute / set volume to 30 percent | `system.volume.set` | 2 | R0 | SYS-AV-01 |
| Turn on Do Not Disturb | `system.dnd.set` | 1 | R0 | SYS-AV-02 |
| Lock the screen | `system.lock` | 1 | R0 | SYS-PWR-01 |
| Sleep the display | `system.display.sleep` | 1 | R0 | SYS-PWR-02 |
| Start / stop / restart the dev server | `project.dev.start/stop/restart` | 3 | R1 | TERM-DEV-01…03 |
| Run the tests / unit tests only / watch the tests | `project.test.run` | 3 | R1 | TERM-TEST-01 |
| Install the dependencies | `project.deps.install` | 3 | R1 | TERM-DEV-04 |
| Build the project | `project.build` | 3 | R1 | TERM-DEV-05 |
| Typecheck the project | `project.typecheck` | 3 | R1 | TERM-DEV-06 |
| Open a terminal in this project | `app.terminal.open` | 3 | R0 | TERM-SESS-01 |
| Go to the Vaani project folder in the terminal | `terminal.cd` | 7 | R2 | TERM-SESS-02 |
| Clear the terminal | `terminal.clear` | 7 | R0 | TERM-SESS-03 |
| Run the last command again | `terminal.repeat` | 7 | R3 | TERM-SESS-04 |
| Activate the virtual environment | `terminal.venv.activate` | 7 | R2 | TERM-ENV-01 |
| Export the env from .env | `terminal.env.export` | 7 | R4 | TERM-ENV-02 |
| Use Python 3.12 in this shell | `terminal.python.use` | 7 | R2 | TERM-ENV-03 |
| Don't run it — just show me the command | `dry_run` modifier | — | — | TERM-DRY-01 |
| Create a new branch called… | `vcs.branch.create` | 4 | R1 | CLI-GIT-BRANCH-01 |
| Switch to main | `vcs.checkout` | 4 | R1/R2 | CLI-GIT-02 |
| Pull latest | `vcs.pull` | 4 | R1 | CLI-GIT-03 |
| Commit my changes with a reasonable message | `vcs.commit` | 4+6 | R2 | CLI-GIT-04 |
| Amend the last commit message | `vcs.commit.amend` | 4 | R2 | CLI-GIT-05 |
| Push this branch | `vcs.push` | 4 | R1 | CLI-GIT-06 |
| Force push | `vcs.push.force` | 4 | **R3** | CLI-GIT-07 |
| Discard the changes in this file | `vcs.restore` | 4 | **R3** | CLI-GIT-08 |
| Stash my changes / pop the stash | `vcs.stash.push/pop` | 4 | R1 | CLI-GIT-09 |
| Show the diff | `vcs.diff.show` | 4 | R0 | CLI-GIT-10 |
| Create a pull request | `forge.pr.create` | 4 | R2 | CLI-GH-01 |
| Open the PR I was working on / list my open PRs | `forge.pr.open/list` | 4 | R0 | CLI-GH-02 |
| Check out the PR branch for pull request 3 | `forge.pr.checkout` | 4 | R1 | CLI-GH-03 |
| Merge the pull request | `forge.pr.merge` | 4 | **R3** | CLI-GH-04 |
| Install lodash / add requests to the project | `pkg.add` | 4 | R2 | CLI-PKG-01 |
| Update the lockfile | `pkg.lock` | 4 | R1 | CLI-PKG-02 |
| Run npm run lint / run uv sync | `pkg.script.run` | 4 | R1 | CLI-PKG-03 |
| Remove node_modules and reinstall | `pkg.reinstall` | 4 | **R3** | CLI-PKG-04 |
| Start docker | `container.engine.start` | 4 | R1 | CLI-DOCK-01 |
| List running containers | `container.list` | 4 | R0 | CLI-DOCK-02 |
| Stop all containers | `container.stop_all` | 4 | **R3** | CLI-DOCK-03 |
| Rebuild the compose stack | `container.compose.rebuild` | 4 | R2 | CLI-DOCK-04 |
| Tail logs for the api container | `container.logs` | 4 | R0 | CLI-DOCK-05 |
| Brew update / apt update | `syspkg.update` | 4 | R2/R4 | CLI-SYS-01 |
| Login to gh | `forge.auth.login` | 4 | R1 | CLI-AUTH-01 |
| Run terraform plan | `iac.plan` | 4 | R2 | CLI-IAC-01 |
| Open Gmail / go to Stripe dashboard | `site.open` | 1 | R0 | UI-BROW-01 |
| Open localhost 3000 | `site.open` | 2 | R0 | UI-BROW-02 |
| Search Google for … | `site.search` | 2 | R0 | UI-BROW-03 |
| Open ChatGPT and start a new chat | `site.open` | 1 | R0 | UI-BROW-04 |
| Refresh this tab / close this tab | `browser.tab.reload/close` | 7 | R0/R2 | UI-BROW-05 |
| Open a new incognito window | `browser.window.private` | 1 | R0 | UI-BROW-06 |
| Open this folder in Cursor / VS Code | `editor.open` | 3 | R0 | UI-EDIT-01 |
| Open the file with the build error | `editor.open` (from job log) | 3 | R0 | UI-EDIT-04 |
| Format this file | `project.format` / `editor.format` | 4/7 | R1 | UI-EDIT-02 |
| Go to definition / find references | `editor.nav` | 7 | R0 | UI-EDIT-03 |
| Add logging to this function / refactor this file | `agent.task` | 6 | R2 | UI-EDIT-05 |
| Fix the failing test | `agent.task` | 6 | R2 | UI-EDIT-06 |
| Focus Chrome / focus the terminal | `window.focus` | 4 | R0 | UI-WIN-01 |
| Split the window left | `window.tile` | 4 | R0 | UI-WIN-02 |
| Hide all other windows / show desktop | `window.hide_others` | 4 | R0 | UI-WIN-03 |
| Open Downloads / open Desktop | `files.open_dir` | 1 | R0 | UI-FM-01 |
| New folder on the desktop named screenshots | `files.mkdir` | 2 | R1 | UI-FM-02 |
| Open Slack / Notes / Calendar | `app.open` | 1 | R0 | UI-APP-01 |
| Draft a reply saying I'll send it by Friday | `comms.draft` | 4/6 | R2 | UI-APP-02 |
| Remind me in an hour to stretch | `reminder.create` | 4 | R1 | UI-APP-03 |
| Stop / cancel | `session.cancel` | 0 | R0 | control |
| Undo that | `session.undo` | 0 | varies | §12.10 |
| Use Claude / Cursor / Codex for this | `brain` modifier | — | — | §12.5 |
| How do I …? / where is …? | `guide.point` / `guide.offer` | 5 | R0 | GUIDE-01/02 |

---

## Appendix B — open decisions

Resolve in the slice that needs it; record the answer in that PR.

1. **Workspace precedence** — §5.2 proposes focused-editor → focused-terminal → env → last-used → home. Confirm before S3.
2. **Confirm phrase set** — "confirm" only, or also "yes"/"do it"/"go ahead"? Risk: "yes" appears in ordinary speech near a pending action.
3. **Windows volume** — bundle a helper binary, take a `pycaw`-style COM dependency, or ship `DEGRADED`?
4. **DND** — accept `DEGRADED` on macOS and Windows, or take the Shortcuts/registry hacks?
5. **Wayland** — how much of §10 is permanently `UNSUPPORTED` vs portal-reachable (ROADMAP `P0-06`)?
6. **Brain for rung 5** — vision-capable adapter choice, and whether guide mode works at all without one.
7. **Rung 7 default** — off entirely until a pack is installed (proposed), or off-but-discoverable?
8. **`vaani.toml` vs `~/.config/vaani/commands.json`** — repo-local profile overrides vs a global phrase→argv map (ROADMAP `P2-04`). Proposal: both, repo-local wins.
9. **Overlay toolkit per OS** — reuse the pill's toolkit, or per-OS native overlays (macOS SwiftUI-style, Windows WinUI, GTK)?
10. **Result surface convergence** — does `ResultWindow` become the same window as the history UI (`P2-08`)?
```
