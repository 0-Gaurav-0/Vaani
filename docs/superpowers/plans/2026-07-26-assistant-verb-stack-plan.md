# Assistant verb stack — implementation plan

Spec: [2026-07-26-assistant-command-use-cases.md](../specs/2026-07-26-assistant-command-use-cases.md)
Branch family: `feat/av-*`, `plat/av-*`
Baseline at time of writing: `explore/assistant-use-cases` @ `5c2a066`, **135 unit tests green** (`.venv/bin/python -m pytest tests/unit -q`)

This plan turns the 9 slices (S0–S8) and 20 feature IDs (A-01…A-20) of the spec into PR-sized tasks with real signatures, files, tests, and acceptance gates.

---

## 0. How to use this plan

Every task below is one PR. Task format:

```
T<slice>.<n>  Title                                    [size] [branch]
  Depends:    other task IDs
  Files:      new / modified
  Interface:  the code contract this PR establishes
  Tests:      unit / integration / manual
  Acceptance: the check that says "done"
```

Sizes: **S** ≈ one sitting, **M** ≈ a day, **L** ≈ multi-day (split if it grows).

Rules for all PRs:

1. `.venv/bin/python -m pytest tests/unit -q` stays green at 135+ tests. **No existing test is modified to accommodate a refactor** — if an existing assertion has to change, that is a behavior change and needs to be called out in the PR body.
2. New modules follow house style: `from __future__ import annotations`, frozen dataclasses, `Protocol` for seams, dependency injection over imports-at-use, no new runtime dependencies without a line in the PR body justifying it.
3. Tests mirror module paths: `src/vaani/intent/router.py` → `tests/unit/intent/test_router.py`.
4. Each PR updates the ROADMAP status row it touches.

---

## 1. Current architecture — what the plan has to work around

Five findings from the existing tree that shape every task. These are the reasons this plan starts with refactors instead of features.

### 1.1 `bundle.run` ignores the controller; each runtime builds its own

[`__main__.main()`](../../../src/vaani/__main__.py#L63) calls `bundle.run(None)`, and the bundle is constructed with `run=lambda _controller: run_linux(settings)` ([linux/runtime.py:66](../../../src/vaani/platform/linux/runtime.py#L66)). The real `Controller` is built **inside** each `run_<os>()` — so `run_linux`, `run_macos`, and `run_windows` each independently construct recorder, history, groq, feedback, key provider, `CodexRunner`, and `ResultWindow` ([linux/runtime.py:112-146](../../../src/vaani/platform/linux/runtime.py#L112-L146)).

Meanwhile `build_linux` fills `hotkeys`/`target`/`delivery` with `_NoopHotkeys`/`_NoopTarget`/`_NoopDelivery` placeholders ([linux/runtime.py:70-88](../../../src/vaani/platform/linux/runtime.py#L70-L88)) whose only job is to satisfy the dataclass.

**Consequence:** wiring a registry + router + policy engine into the controller means doing it three times, in three files, forever. **T0.1 must land first.**

### 1.2 Routing is interleaved with the request lifecycle

`Controller._process` ([controller.py:227-347](../../../src/vaani/controller.py#L227-L347)) does transcription, answer-prefix handling, app routing, site routing, browser allowlist, Codex fallback, history writes, state transitions, and feedback in one 120-line method with three separate `with self._lock:` history-insert blocks.

The uncommitted working-tree diff is a perfect illustration: it adds nine more string literals to the `_browser_intent` allowlist ([controller.py:350-373](../../../src/vaani/controller.py#L350-L373)). That set becomes one grammar pattern in T0.4 and the method disappears.

**Consequence:** land or discard the current working diff before starting T0.3, because they touch the same lines.

### 1.3 The only recurring loop dies when processing ends

The amplitude monitor thread is the sole poller of the pill's control file ([controller.py:179-219](../../../src/vaani/controller.py#L179-L219)), and `_process`'s `finally` sets `_amplitude_stop` ([controller.py:345-347](../../../src/vaani/controller.py#L345-L347)). So once a request completes, nothing in the process is watching for user input except the OS hotkey loop.

A `PendingAction` has to survive *after* `PROCESSING` ends — that is exactly when the user is being asked to confirm.

**Consequence:** the confirmation engine needs a process-lifetime loop, not a per-request one. **T0.2.**

### 1.4 History is a fixed 8-column table, and `cleanup_status` is doing routing-label duty

`_migrate` creates 8 columns at `user_version=1` with one ad-hoc `ALTER` for `duration_ms` ([history.py:46-61](../../../src/vaani/history.py#L46-L61)). The controller writes `cleanup_status="app_action"` / `"browser_action"` as a route tag ([controller.py:267](../../../src/vaani/controller.py#L267)), and `tests/unit/test_controller.py:140` asserts it.

**Consequence:** verb-level audit needs a `user_version=2` additive migration, and `app_action` must be written *alongside* the new columns for one release so that existing assertion keeps passing untouched (rule 1).

### 1.5 CLI arg handling is `in sys.argv` sniffing

`main()` checks `"--record" in sys.argv[1:]` and `"--debug" in sys.argv[1:]` ([__main__.py:52-55](../../../src/vaani/__main__.py#L52-L55)). `docs/install/*` reference `--record`, so those spellings are load-bearing.

**Consequence:** `vaani do` / `vaani caps` need a real subcommand layer that keeps bare `vaani` launching the daemon and keeps `--record`/`--debug` working as aliases. **T0.5.**

### 1.6 Assets worth reusing rather than rebuilding

| Existing | Reuse for |
|---|---|
| `_stop_process` — cross-platform process-group kill, already handles `os.name == "nt"` and missing `killpg` ([codex.py:19-39](../../../src/vaani/codex.py#L19-L39)) | The supervisor's stop path (T3.1) — lift, don't rewrite |
| `_redact` for api-key/token patterns ([codex.py:15-17](../../../src/vaani/codex.py#L15-L17)) | Audit + evidence redaction (T0.6) |
| `child_environment()` env allowlist ([config.py:115-131](../../../src/vaani/config.py#L115-L131)) | The verb runner's child env (T0.6) |
| `indicator_protocol` — atomic tmp+`os.replace` JSON control channel with a validated command enum ([indicator_protocol.py:46-58](../../../src/vaani/indicator_protocol.py#L46-L58)) | The confirm channel (T0.2) and the overlay bus (T6.2) |
| `Settings` per-OS roots ([config.py:33-48](../../../src/vaani/config.py#L33-L48)) | New paths: `jobs.json`, `packs.json`, `overlay.json` (T0.5) |
| `resolve_app`'s exclude-list technique (`" website"`, `" in brave"`) ([apps.py:52](../../../src/vaani/apps.py#L52)) | Grammar `exclude` rule — the precedence logic in spec §4.2 already exists in prototype form |

---

## 2. Target architecture

### 2.1 Request flow

```
hotkey hold ──► recorder ──► groq.transcribe ──► raw transcript
                                                      │
                            ┌─────────────────────────┴──────────────────────────┐
                            │  1. policy.pending?  → confirm / reject path        │
                            │  2. answer-prefix?   → answer path (P0-01)          │
                            │  3. mode classify    → act | guide  (spec §1.2)     │
                            │  4. intent.router    → ladder rungs 1→7 (spec §4)   │
                            └─────────────────────────┬──────────────────────────┘
                                                      ▼
                        context.resolve(verb.requires)  ──► Context
                                                      ▼
                        policy.check(verb, intent)  ──► ok | NEEDS_CONFIRM | REFUSED | UNSUPPORTED
                                                      ▼
                        verb.handler(intent, context) ──► Result
                                                      ▼
                        surface.emit(Result) + audit.write(Result)
```

### 2.2 Module map with import direction

Strict layering. **A lower layer never imports a higher one.** Enforced by T0.7's import-graph test.

```
L8  surface/bridge.py · cli.py                      ─┐
L7  policy/audit.py · history.py                     │
L6  surface/{pill,result,overlay}.py                 │  may import ↓
L5  exec/{runner,proc,supervisor,agent,input}.py     │
L4  policy/{risk,confirm,dryrun,disambiguate,undo}.py│
L3  verbs/{registry,packs/*}.py                      │
L2  context/{workspace,repo,project,focus,screen}.py │
L1  intent/{schema,normalize,lexicon,grammar,llm,router}.py
L0  controller.py · audio · groq · types · config    ─┘
platform/<os>/  — leaf adapters, imported by L2/L5/L6 through protocols only
```

`intent/schema.py` holds `Intent`, `Context`, `Verb`, `Result`, `RiskClass`, `Support` — it is the one module nearly everything imports, so it takes **no** intra-project imports beyond `platform/protocol.py`'s `PlatformId`.

### 2.3 New `PlatformBundle` surfaces

Five optional fields, each `None` when the OS can't provide it — absence *is* the capability signal:

```python
@dataclass
class PlatformBundle:
    ...                          # existing 10 fields unchanged
    system: SystemControl | None = None    # volume, dnd, lock, wifi, dns, trash, ip
    window: WindowControl | None = None    # focus, tile, hide_others
    input: InputSynth | None = None        # rung 7 keystrokes
    terminal: TerminalOpener | None = None # open a terminal at a cwd
    screen: ScreenCapture | None = None    # rung 5 frames
```

Defaults keep every existing `build_<os>()` call site valid, so T0.8 is additive.

---

## 3. Slice S0 — the spine (no new user-visible verbs)

**Goal:** today's exact behavior, re-expressed on the new architecture. If a user notices anything, S0 failed.

### T0.1 Extract shared controller assembly [M] `plat/av-assemble`

- **Depends:** —
- **Files:** new `src/vaani/assemble.py`; modified `platform/{linux,macos,windows}/runtime.py`, `__main__.py`
- **Interface:**
  ```python
  @dataclass(frozen=True)
  class Assembly:
      controller: Controller
      history: HistoryStore
      groq: GroqClient
      logger: logging.Logger

  def assemble(bundle: PlatformBundle, *, delivery=None, target=None,
               hotkeys=None, logger=None) -> Assembly: ...
  ```
  Each `run_<os>()` keeps **only** OS event-loop code: display/message-pump setup, hotkey registration, signal handlers, banner printing. Everything from `history = HistoryStore(...)` through `controller.result_window = ...` moves into `assemble`.
- **Tests:** `tests/unit/test_assemble.py` — a fake bundle produces a wired controller; asserts all three runtimes call `assemble` (import-level check that the duplicated construction is gone: `grep -c "HistoryStore(" platform/*/runtime.py == 0`).
- **Acceptance:** 135 tests green; `run_linux` shrinks by ~35 lines; the three runtimes contain no `Controller(` call of their own.

### T0.2 Process-lifetime session loop [M] `feat/av-session-loop`

- **Depends:** —
- **Files:** new `src/vaani/session_loop.py`; modified `controller.py`
- **Interface:**
  ```python
  class SessionLoop:
      """One 50ms daemon thread for the process lifetime. Jobs are callables."""
      def add(self, name: str, fn: Callable[[], None], *, every: float = 0.05) -> None: ...
      def start(self) -> None: ...
      def stop(self) -> None: ...
  ```
  The amplitude writer and `_poll_indicator_control` become two registered jobs instead of the body of `_start_amplitude_monitor`. The amplitude job self-gates on `state is RECORDING` (as it already does at [controller.py:189-192](../../../src/vaani/controller.py#L189-L192)); the control-file job runs always.
- **Why:** §1.3 — pending-action confirm and TTL expiry must tick while the controller is IDLE.
- **Tests:** loop starts/stops idempotently; a job raising doesn't kill the loop or its siblings; control commands are honored while IDLE (**new capability**, cannot happen today); amplitude values still written during RECORDING.
- **Acceptance:** existing pill stop/cancel behavior unchanged in manual test on Linux; `_amplitude_thread` is gone.

### T0.3 Core contracts [S] `feat/av-contracts`

- **Depends:** —
- **Files:** new `src/vaani/intent/{__init__,schema}.py`
- **Interface:** `Intent`, `Context`, `Verb`, `SlotSpec`, `Result`, `Status`, `RiskClass`, `Support`, `PendingAction`, `UndoToken`, `OverlayOp` exactly as spec §5. All frozen. `Result.summary` validated ≤ 80 chars in `__post_init__` (truncate, don't raise — a long summary must never break a working action).
- **Tests:** construction, immutability, summary truncation, `Status` round-trips through `str`.
- **Acceptance:** module imports nothing from `vaani` except `platform.protocol.PlatformId`.

### T0.4 Registry + grammar + ladder router [L] `feat/av-router`

- **Depends:** T0.3
- **Files:** new `src/vaani/verbs/registry.py`, `src/vaani/verbs/packs/core.py`, `src/vaani/intent/{normalize,grammar,router}.py`; modified `controller.py`
- **Interface:**
  ```python
  # verbs/registry.py
  class Registry:
      def register(self, verb: Verb) -> None: ...
      def get(self, name: str) -> Verb | None: ...
      def enabled(self, platform: PlatformId) -> tuple[Verb, ...]: ...   # pack + support filtered
      def matrix(self) -> dict[str, dict[str, tuple[Support, str]]]: ...  # for `vaani caps`

  # intent/grammar.py — declarative, data-first, `re` only
  @dataclass(frozen=True)
  class Pattern:
      verb: str
      any_of: tuple[tuple[str, ...], ...] = ()   # each group needs one phrase hit
      require: tuple[str, ...] = ()              # all phrases must appear
      exclude: tuple[str, ...] = ()              # any hit kills the match
      slots: tuple[SlotRule, ...] = ()
      priority: int = 0                          # spec §4.2 precedence
  def match(text: str, patterns: Sequence[Pattern]) -> tuple[str, dict, int] | None: ...

  # intent/router.py
  class Router:
      def route(self, transcript: str, *, platform: PlatformId) -> Intent | None: ...
  ```
- **Migration content:** three verbs only, reproducing today's behavior exactly —
  - `app.open` ← wraps `bundle.apps.resolve/launch`, keeping the action-word requirement and the `website|web app|in brave|in chrome|browser` exclude list from [apps.py:50-53](../../../src/vaani/apps.py#L50-L53)
  - `site.open` ← wraps `resolve_site` + `bundle.browser.open`, keeping the `brave` default and `chrome` override from [controller.py:274-281](../../../src/vaani/controller.py#L274-L281)
  - `browser.open` ← the 18-phrase exact allowlist from `_browser_intent` becomes one `Pattern` with `any_of`
  - `agent.task` ← the `CodexRunner` fallback, rung 6, unchanged semantics
- **`_process` after this PR:** transcribe → answer-prefix → `if mode == "assistant": self._dispatch(raw, audio)` → dictation path. `_dispatch` is ~15 lines: route, resolve context, check policy, call handler, write history, emit. `_browser_intent` and `_open_browser` are deleted.
- **Tests:**
  - `tests/data/utterances.yaml` — the spec's Appendix A corpus as fixtures: utterance → expected verb, slots, rung. Seeded in this PR with every phrase from today's allowlists plus the must-NOT-match negatives from spec §7–§11.
  - Router precedence: "open Claude" → `app.open`; "open Claude website" → `site.open`; both asserted from the corpus file.
  - **Brain-mocked-to-raise test**: every rung-1/2 corpus row resolves with the LLM parser raising on call (spec §13.5 invariant 2).
- **Acceptance:** `tests/unit/test_controller.py` passes **unmodified**, including the `cleanup_status == "app_action"` assertion at line 140 — meaning the compatibility write is preserved.

### T0.5 Settings paths + CLI subcommands [M] `feat/av-cli`

- **Depends:** T0.3, T0.4
- **Files:** new `src/vaani/cli.py`; modified `config.py`, `__main__.py`, `pyproject.toml` (no new deps — `argparse`)
- **Interface:**
  ```
  vaani                              # daemon (unchanged default)
  vaani --record | vaani record      # back-compat alias kept, docs/install/* still valid
  vaani do <verb> [--slot k=v ...] [--json] [--dry-run] [--yes]
  vaani caps [--json]
  ```
  New `Settings` properties: `jobs_path`, `packs_path`, `overlay_path`, `vocab_path` — all under existing `data_dir`/`cache_dir` so per-OS roots come for free.
- **Why:** `vaani do` is how every subsequent slice is tested without a microphone. It is not a convenience feature; it is the test harness.
- **Tests:** subcommand dispatch; `--record` and `--debug` still work in both spellings; `do` on an unknown verb exits 2 with the catalog; `--json` output is stable and machine-parseable; `caps` has no blank cells.
- **Acceptance:** `vaani do app.open --slot name=Terminal --json --dry-run` prints the argv it would run, on all three OSes.

### T0.6 Verb runner + shared proc/redaction [S] `feat/av-runner`

- **Depends:** T0.3
- **Files:** new `src/vaani/exec/{__init__,proc,runner}.py`; modified `codex.py` (import the lifted helpers), `observability.py`
- **Interface:**
  ```python
  @dataclass(frozen=True)
  class Command:
      argv: tuple[str, ...]
      cwd: Path | None = None
      timeout: float = 20.0
      env_extra: Mapping[str, str] = field(default_factory=dict)

  @dataclass(frozen=True)
  class Completed:
      argv: tuple[str, ...]; returncode: int; stdout: str; stderr: str
      timed_out: bool = False; cancelled: bool = False

  def run(cmd: Command, *, cancel: threading.Event | None = None) -> Completed: ...
  def powershell(script_args: Sequence[str]) -> tuple[str, ...]: ...  # the ONE string-command site
  ```
  `_stop_process` and `_redact` move from `codex.py` to `exec/proc.py` and `observability.py`; `codex.py` imports them. Env = `child_environment()` + `env_extra`, never inherited wholesale.
- **Tests:** argv is never joined; `shell=True` appears nowhere (repo-wide grep test); timeout kills the group; cancel event interrupts; redaction applied to both streams; `powershell()` quoting fuzzed against injection-shaped slot values.
- **Acceptance:** `codex.py` behavior byte-identical (its 100% of existing tests pass unmodified).

### T0.7 Invariant tests [S] `feat/av-invariants`

- **Depends:** T0.4, T0.6
- **Files:** new `tests/unit/test_invariants.py`
- **Content:** the seven invariants from spec §13.5 as executable tests — no `shell=True` / no joined argv anywhere in `src/`; import-graph layering (§2.2) via `ast` parsing; every registered verb declares all three platforms; `DEGRADED` requires a non-empty reason; no R2+ verb reachable without a `PendingAction`; corpus resolves with the brain mocked to raise.
- **Acceptance:** these tests run in every subsequent PR and are the structural guard rail for slices S1–S8.

### T0.8 Bundle surface extension [S] `plat/av-bundle-surfaces`

- **Depends:** T0.3
- **Files:** modified `platform/protocol.py`, `platform/{linux,macos,windows}/runtime.py`
- **Content:** the five optional protocols from §2.3, all defaulting to `None`. No implementations yet — this PR only opens the sockets so S1 can fill them per-OS in parallel.
- **Acceptance:** additive; every existing call site unchanged.

**S0 gate:** 135 existing tests green and unmodified; `Controller._process` under 60 lines with zero verb knowledge; `vaani caps --json` lists 4 verbs across 3 OSes with no blanks; `vaani do site.open --slot url=https://example.com` works.

---

## 4. Slice S1 — safe system verbs

**Goal:** the everyday R0 set on all three OSes, and the capability matrix proven honest.

### T1.1 Normalization + lexicon [M] `feat/av-normalize`
- **Depends:** T0.4 · **Files:** `intent/{normalize,lexicon}.py`, `Settings.vocab_path`
- Number words → digits (`three thousand`→3000, `eighty eighty`→8080, `thirty percent`→30); separator words (`slash`/`dash`/`underscore`/`dot`); filler stripping (`umm`, `please`, `can you`) applied to the *matching* copy only, never to the stored raw transcript; tech lexicon + user vocabulary file (ROADMAP `P2-09`).
- **Tests:** ~60-row table of spoken→normalized, including `"open localhost 3000"` **not** yielding a bare port slot (spec SYS-PORT-01 negative case).

### T1.2 Per-OS `SystemControl` [L, parallelizable 3 ways] `plat/av-system-{linux,macos,windows}`
- **Depends:** T0.6, T0.8
- **Interface:** `volume_set(pct) · mute(bool) · dnd(bool) · lock() · display_sleep() · wifi(bool) · dns_flush() · trash_empty() · local_ip() -> str` — each returning `Result`, each declaring its own `Support`.
- Mechanisms per spec §7.2. **Required honesty outcomes:** Windows volume ships `DEGRADED` unless the pycaw/helper decision (spec Appendix B #3) lands first; DND ships `DEGRADED` on macOS and Windows; macOS `dns_flush` registers as **R4 disabled** because it needs sudo.
- **Tests:** argv shape per OS against fake runners; `UNSUPPORTED` returns a reason string and never escalates rung (invariant 5).

### T1.3 Core verb pack: apps, sites, files, dirs [M] `feat/av-pack-core`
- **Depends:** T1.1, T1.2
- Verbs: `app.open`, `app.quit`, `system.{volume.set,dnd.set,lock,display.sleep,ip.copy}`, `files.{reveal,open_dir}`, `site.{open,search}`, `browser.window.private`.
- `files.open_dir` uses per-OS known-folder resolution, **not** `~/Downloads` string-building.
- `system.ip.copy` is the only `act` verb touching the clipboard — it goes through `bundle.delivery`, reusing the existing snapshot/paste guard.
- **Tests:** corpus rows for all of UC SYS-APP-01/02, SYS-AV-01, SYS-PWR-01/02, SYS-NET-02, SYS-FILE-05, UI-FM-01, UI-BROW-01/02/03/06.

### T1.4 Result surface: pill states + result window parity [M] `feat/av-surface` (ROADMAP `P0-05`)
- **Depends:** T0.2, T0.3
- `surface/result.py` renders `Result` consistently across the three feedback adapters; replaces the ad-hoc `show_assistant_result` closures currently duplicated in each runtime ([linux/runtime.py:141-146](../../../src/vaani/platform/linux/runtime.py#L141-L146)). Pill gains `confirming` and `working` phases alongside today's `recording`/`processing` (`indicator_protocol.ALLOWED_PHASES` extended, with the validated-enum pattern kept).
- **Tests:** phase round-trip; unknown phase falls back to `recording` (existing behavior at [indicator_protocol.py:125-130](../../../src/vaani/indicator_protocol.py#L125-L130)).

**S1 gate:** "open Terminal", "mute", "set volume to 30 percent", "lock the screen", "reveal this project in Finder/Explorer/Files", "open Gmail" work on all three OSes or report `DEGRADED`/`UNSUPPORTED` with a reason. `vaani caps --json` matches reality.

---

## 5. Slice S2 — confirmation and the first mutating verbs

### T2.1 Confirmation engine [L] `feat/av-confirm` (ROADMAP `P2-05`)
- **Depends:** T0.2, T0.7
- **Interface:**
  ```python
  @dataclass(frozen=True)
  class PendingAction:
      id: str; verb: str; slots: Mapping[str, Any]
      materialized: tuple[str, ...]      # exact argv shown to the user
      risk: RiskClass; expires_at: float

  class ConfirmEngine:
      def stage(self, intent: Intent, verb: Verb, materialized) -> PendingAction: ...
      def approve(self, action_id: str, *, via: str) -> PendingAction | None: ...  # single-use
      def reject(self, action_id: str) -> None: ...
      def expire_tick(self) -> None: ...   # registered on SessionLoop
  ```
- **Three approval paths, all required (spec §12.2 / A9):**
  1. **Hotkey tap** — a press/release shorter than `MIN_RECORDING_SECONDS` (0.250s, [config.py:18](../../../src/vaani/config.py#L18)) while a pending action exists = approve. No STT round trip, hands-free-adjacent, and it reuses the hold-to-talk press/release seam already in each runtime ([linux/runtime.py:148-163](../../../src/vaani/platform/linux/runtime.py#L148-L163)).
  2. **Voice** — a normal utterance is checked against the confirm phrase set **before** the router runs (spec §1.2 step 1 ordering).
  3. **Click** — pill writes `approve:<id>` / `reject:<id>` through the `indicator_protocol` channel; `ALLOWED` gains the two commands with the same enum validation.
- Single-use, TTL ~20s, invalidated by any new utterance or a target state change. The brain can never self-approve R3/R4 — enforced in `ConfirmEngine.approve` by rejecting `via="agent"` for those classes, and tested.
- **Tests:** TTL expiry; double-approve rejected; new utterance invalidates; tap-vs-hold discrimination at the 250ms boundary; `via="agent"` blocked on R3/R4.

### T2.2 Dry-run + materialization [S] `feat/av-dryrun`
- **Depends:** T2.1
- Modifier detected in `normalize` ("don't run it", "just show me", "what would you run"), plus `--dry-run` on `vaani do`. A dry run returns `Status.DRY_RUN` with `evidence` = the argv and the resolved context, and **never** calls the handler — enforced by dispatching through a wrapper, not by asking handlers to behave.
- **Tests:** every registered verb, parameterized: dry-run never invokes its handler (a spy asserts zero calls).

### T2.3 Undo stack [M] `feat/av-undo`
- **Depends:** T2.1
- Bounded 10-entry stack; three classes per spec §12.10. `session.undo` verb. Irreversible verbs return `REFUSED` with "killing a process can't be undone" rather than a partial attempt.
- **Tests:** inverse pairs (volume, DND, wifi, mkdir, file sweep, stash/pop, branch create/delete); irreversible refusal text; expiry.

### T2.4 Ports, processes, trash [M] `feat/av-pack-procs`
- **Depends:** T2.1, T1.2
- Verbs: `system.port.free` (fully specified in spec §7.1), `system.proc.kill`, `system.proc.top`, `system.trash.empty`, `system.wifi.set`.
- `system.proc.kill` confirm lists **all** matches with a count and refuses >5 without `force`.
- **Tests:** integration — bind a real socket on an ephemeral port in a fixture, free it, assert released; the "already free" and "owned by another user" branches; per-OS argv (`lsof`/`ss`/`Get-NetTCPConnection`).

### T2.5 Guide-offer for interrogatives [M] `feat/av-guide-offer`
- **Depends:** T2.1, T0.4
- Spec §1.2 rules 2–3 + GUIDE-02: "what's on port 3000" / "how do I free port 3000" → find, report, **offer**. No screen capture needed, so this lands here and not in S6. This is the safety valve that makes S2's mutating verbs safe to ship.
- **Tests:** every mutating verb in the corpus, phrased interrogatively, produces `NEEDS_CONFIRM` and calls no handler.

**S2 gate:** "free port 3000" works on three OSes; confirm reachable by tap, voice, and click; dry-run on every verb; "how do I free port 3000" offers instead of acting.

---

## 6. Slice S3 — project context and managed jobs

### T3.1 Process supervisor [L] `feat/av-supervisor` (ROADMAP `P2-04`)
- **Depends:** T0.6
- **Interface:**
  ```python
  @dataclass(frozen=True)
  class Job:
      key: str; verb: str; argv: tuple[str, ...]; cwd: str
      pid: int; started_at: float; log_path: str
      port: int | None = None; status: str = "running"

  class Supervisor:
      def start(self, key: str, cmd: Command) -> Job: ...
      def stop(self, key: str, *, forceful: bool = False) -> bool: ...
      def restart(self, key: str) -> Job: ...
      def status(self, key: str) -> Job | None: ...
      def logs(self, key: str, *, tail: int = 200) -> str: ...
      def adopt_or_clear(self) -> None: ...   # on boot
  ```
- Registry at `Settings.jobs_path`, written with the atomic tmp+`os.replace` idiom from `indicator_protocol`. Detached spawn: `start_new_session=True` on POSIX, `CREATE_NEW_PROCESS_GROUP` on Windows. Stop path reuses the lifted `exec/proc.stop_process` — the escalation (SIGTERM → 2s → SIGKILL) lives here, once.
- Logs to a bounded file (keep last 256 KB by truncate-and-rotate; a dev server left running for a week must not fill a disk).
- Port discovery: bounded regex over the log tail for `https?://(localhost|127\.0\.0\.1):(\d+)`, capped at the first 200 lines, later cross-checked against the port-holder lookup from T2.4.
- Boot adoption: pid alive **and** argv matches → adopt; otherwise clear the entry. Never adopt on pid alone (pid reuse).
- **Tests:** lifecycle; restart preserves argv; registry survives a simulated Vaani restart; pid-reuse rejection; log truncation; group-kill leaves no orphan (integration, POSIX + Windows).

### T3.2 Workspace, repo, focus context [L, parallelizable] `feat/av-context` (ROADMAP `P2-07`)
- **Depends:** T0.8
- `context/workspace.py` implements the precedence from spec §5.2 and **always reports `workspace_source`** in the Result (invariant 7). `context/repo.py` shells `git rev-parse --show-toplevel` through the runner. `context/focus.py` is per-OS: macOS AX API, Windows UIA, Linux X11 (`_NET_ACTIVE_WINDOW` via the existing `X11Probe`), Wayland → `UNSUPPORTED`.
- **Tests:** precedence table; focus adapters against fakes; the wrong-workspace scenario is an explicit manual checklist item.

### T3.3 Project profile [M] `feat/av-project-profile`
- **Depends:** T3.2
- `ProjectProfile{root, manager, test, build, dev, typecheck, lint, compose_file, env_files}`; detection order per spec §12.8, cached by root with mtime invalidation, overridable by `vaani.toml`. **A miss refuses and lists the files it checked** — never guesses `npm test` in a Python repo.
- **Tests:** ~10 fixture repo shapes (npm/pnpm/yarn, uv/poetry/hatch, Makefile, mixed JS+Python monorepo, none); assert the refusal message names the checked files.

### T3.4 Project verb pack [M] `feat/av-pack-project`
- **Depends:** T3.1, T3.3
- Verbs: `project.{dev.start,dev.stop,dev.restart,test.run,build,typecheck,deps.install}`, `job.{list,logs}`, `app.terminal.open`, `editor.open`.
- `project.dev.stop` handles the unmanaged case: no job registered but the port is held → fall back to T2.4's port-free with an R2 confirm.
- `editor.open` from a job log (UC UI-EDIT-04) parses `file:line` out of the last failing job — the supervisor payoff.
- **Tests:** fake repo with a passing and a failing suite; assert summary, exit code, and the failing test names in `detail`; cancel mid-run.

**S3 gate:** start/stop/restart the dev server and run the tests on all three OSes; profile refuses instead of guessing; "open the file with the build error" works after a failed run.

---

## 7. Slice S4 — git, forge, packages, containers

### T4.1 Capability packs [M] `feat/av-packs` (ROADMAP `P2-02`)
- **Depends:** T0.4 · Pack registry at `Settings.packs_path`; `core` always on; `git`/`pkg`/`docker`/`forge`/`iac`/`browser-cdp`/`guide`/`computer-use` installable and individually toggleable, each declaring binaries, auth, and permissions. `vaani caps` reflects pack state. A missing binary produces "gh isn't installed", never a rung-6 escalation (invariant 5).

### T4.2 Git pack [L] `feat/av-pack-git`
- **Depends:** T2.1, T3.2, T4.1
- All of spec §9.2 git rows. Always `git -C <repo.root>` argv. **Slugification is the load-bearing piece** (UC CLI-GIT-BRANCH-01): "assistant use cases" → `assistant-use-cases`, "feature slash port killer" → `feature/port-killer`, with per-repo convention inferred from existing branch names.
- `vcs.commit` generates the message via the brain from the diff and **shows it verbatim before committing** — the flagship materialize-then-confirm case.
- `vcs.push.force` is `--force-with-lease` only, R3, argv displayed. `vcs.restore` needs `focus.document_path` and shows the line delta.
- **Tests:** ~30-row slug table; `git check-ref-format` rejection; temp-repo integration for create/collision/dirty-tree; a test asserting `--force` (bare) appears nowhere in the pack.

### T4.3 Forge pack [M] `feat/av-pack-forge`
- **Depends:** T4.2 · `forge.pr.{create,open,list,checkout,merge}`, `forge.auth.login`. `gh` auth precondition surfaces as a `DEGRADED` reason. `forge.pr.merge` is R3 and shows repo/number/title/strategy.

### T4.4 Package + container packs [M, parallelizable] `feat/av-pack-{pkg,docker}`
- **Depends:** T3.3, T4.1
- Manager resolved from the **lockfile**, never from speech. `pkg.add` is R2 because a mis-heard package name is a typosquat risk. `pkg.reinstall` (R3) hard-guards that the deleted path is inside `workspace` — a dedicated test with `../` and symlink attempts.
- Container service names resolve against the compose file so mishears become disambiguation, not misses. `container.stop_all` is R3 and lists names.

### T4.5 Disambiguation [M] `feat/av-disambiguate`
- **Depends:** T2.1 · At most 3 options in the pill, selectable by voice ("the first one") or hotkey; timeout cancels, never defaults. Wired into: multiple removable drives, several matching processes, ambiguous workspace, several matching PRs, unknown project name.

**S4 gate:** the full git flow by voice — branch, commit with a generated-then-shown message, push, PR — with every R3 verb showing exact argv first.

---

## 8. Slice S5 — windows, focus, keystroke macros

### T5.1 `WindowControl` per OS [L, parallelizable 3 ways] `plat/av-window-{linux,macos,windows}`
- macOS `activate` via AX (Accessibility permission); Windows `SetForegroundWindow` with the foreground-lock caveat documented; X11 `wmctrl`/`xdotool` equivalents via the existing Xlib dependency; **Wayland → `UNSUPPORTED` with a reason**.
- Verbs: `window.{focus,tile,hide_others}` (UC UI-WIN-01/02/03).

### T5.2 `InputSynth` + rung 7 gating [L] `feat/av-input-synth`
- **Depends:** T2.1, T4.1
- Keystroke injection behind the `computer-use` pack, **off by default**. Every rung-7 `Result` carries the literal caveat `typed into the focused terminal; outcome not verified` in `detail` (spec §8 family rule).
- Verbs: `browser.tab.{reload,close}`, `editor.{format,nav}`, `terminal.{cd,clear}`. `terminal.repeat` is **refused by default** (spec TERM-SESS-04 — Vaani cannot know what it would re-run). `terminal.env.export` is R4 and never logged.
- **Tests:** pack-disabled → `UNSUPPORTED`; every rung-7 result contains the caveat; focus precondition failure aborts before sending keys.

### T5.3 Prefer verifiable routes over macros [S] `feat/av-format-verb`
- `project.format` (rung 4, runs the project formatter and can verify) takes precedence over `editor.format` (rung 7 macro) whenever a profile formatter exists — a concrete instance of the ladder rule.

**S5 gate:** "focus Chrome" and "open a terminal in this project" work on macOS/Windows/X11; Wayland reports `UNSUPPORTED` cleanly; no rung-7 verb runs without confirm.

---

## 9. Slice S6 — guide mode

### T6.1 Screen capture provider [L] `plat/av-screen-{macos,windows,linux}`
- **Depends:** T3.2 · macOS ScreenCaptureKit (Screen Recording permission), Windows Graphics.Capture, Linux X11 shm; Wayland via portal or `UNSUPPORTED`.
- **Hard constraints, tested:** frame held in memory only, **never written to disk**, never entered into history; capture happens only while the hotkey is held; multi-display and fractional scaling produce correct physical-pixel bounds.

### T6.2 Overlay bus [M] `feat/av-overlay`
- **Depends:** T1.4 · Extends the `indicator_protocol` pattern with `overlay.json`: an op list plus a monotonic `seq`, atomic replace, polled by the existing per-OS `indicator_app.py` pill processes. Ops `point{x,y,label}`, `caption{x,y,text}`, `clear`, per OpenClicky's `[POINT:x,y:label]` protocol. Coordinates validated against the captured frame's bounds **before** rendering; out-of-bounds is rejected, not clamped.

### T6.3 Guide verbs [L] `feat/av-pack-guide`
- **Depends:** T6.1, T6.2
- `guide.point` (rung 5, vision brain), `guide.explain_job` (no screen — reads the last job log, so it's cheap and deterministic), `guide.capabilities` (renders the matrix, per GUIDE-05), `guide.last_result`.
- **Tests:** fixed screenshot fixture + mocked ops → deterministic overlay assertions; a test that greps the capture path for any filesystem write.

**S6 gate:** "where's the export button" points correctly on a scaled multi-display setup; nothing writes a frame to disk.

---

## 10. Slice S7 — agent

### T7.1 Agent runner v2 [L] `feat/av-agent-runner` (ROADMAP `P2-06`)
- **Depends:** T4.1, T2.1
- Replaces the one-shot 30s `CodexRunner` ([codex.py:42](../../../src/vaani/codex.py#L42)) with: pluggable backends (Codex / Claude / Cursor), **the verb catalog as the tool list** (spec §5.3 — the agent calls `project.test.run`, not shell), sessions with ~10-minute continuity (continue vs spawn, per OpenClicky), progress within 1s and streaming thereafter, and the existing cancel/timeout/redaction guarantees preserved.
- **Hard rule, tested:** the agent cannot approve R3/R4 verbs (enforced in T2.1's `approve(via=...)`).
- Wake phrase "Vaani, agent: …" forces rung 6 and skips the router; "use Claude/Codex/Cursor for this" additionally selects the brain.

### T7.2 Agent-backed verbs [M] `feat/av-agent-verbs`
- `agent.task` for UC UI-EDIT-05/06 seeded with the last failing job's output — that seeding is what makes "fix the failing test" actually work. Diffs shown before they're applied.

**S7 gate:** "fix the failing test" runs with the test job's failure as context, shows a diff, and requires confirm; the brain cannot self-approve a force push.

---

## 11. Slice S8 — audit and remote

### T8.1 History v2 [M] `feat/av-history-v2` (ROADMAP `P2-08`)
- **Depends:** T0.4
- `PRAGMA user_version` 1 → 2 with additive `ALTER`s inside the existing EXCLUSIVE transaction, following the `duration_ms` precedent ([history.py:55-59](../../../src/vaani/history.py#L55-L59)). New columns: `verb, rung, risk, status, confirmed_by, workspace, workspace_source, brain, evidence`.
- **Compatibility:** keep writing `cleanup_status="app_action"`/`"browser_action"` for one release so `tests/unit/test_controller.py:140` stays untouched. Removal is its own later PR with its own test change.
- Redaction at the audit boundary via the lifted `_redact`; slots redacted before storage.
- **Tests:** migration from a v1 db with rows; both old and new columns readable; secret-shaped slot values never stored.

### T8.2 Bridge [L] `remote/av-bridge` (ROADMAP `P3-01`, `P3-02`)
- **Depends:** T8.1, T6.2
- stdlib `http.server` on `127.0.0.1`, bearer token required, refuses a public bind. `POST /v1/intent`, `GET /v1/caps`, `GET /v1/jobs`, `POST /v1/confirm/{id}`, `POST /v1/overlay`, `GET /events` (SSE) — the output primitives borrowed from OpenClicky's control bridge, on the same policy engine.
- **Hard rule, tested:** a remote R2+ intent requires the same confirmation as a local one; audit records the caller.

**S8 gate:** the same verb produces an identical `Result` through voice, `vaani do`, HTTP, and an agent tool-call.

---

## 12. Critical path and parallelism

```
T0.1 ─┬─► T0.4 ─┬─► T0.5 ─► T1.3 ─┬─► T2.1 ─┬─► T2.4 ─► T3.4 ─► T4.2 ─► T7.1
T0.2 ─┤         │                  │         ├─► T2.2
T0.3 ─┴─► T0.6 ─┴─► T0.7           │         ├─► T2.3
      └─► T0.8 ─► T1.2 ────────────┘         └─► T2.5
                  T1.1 ─────────────┘
                  T3.1 · T3.2 (parallel with S2)
```

**Critical path:** T0.1 → T0.4 → T2.1 → T3.1/T3.4 → T4.2 → T7.1. Everything else can be scheduled around it.

**Genuinely parallel work** (no shared files):
- T1.2, T5.1, T6.1 split three ways by OS — three people, no conflicts, since each touches only `platform/<os>/`.
- T3.1 (supervisor) and T3.2 (context) are independent of the whole S2 confirm chain.
- T4.4's `pkg` and `docker` packs are independent of each other and of T4.2/T4.3.
- Corpus growth (`tests/data/utterances.yaml`) is append-only, so every slice adds rows without conflicting.

**Rough weight:** S0 ≈ 8 PRs (1 L, 4 M, 3 S) · S1 ≈ 4 (1 L ×3 OS) · S2 ≈ 5 · S3 ≈ 4 (2 L) · S4 ≈ 5 · S5 ≈ 3 · S6 ≈ 3 · S7 ≈ 2 · S8 ≈ 2. Roughly 36 PRs, of which S0–S3 (21) deliver the daily-value core.

---

## 13. Testing and CI

### 13.1 New fixture assets

| Asset | Purpose |
|---|---|
| `tests/data/utterances.yaml` | The spec's Appendix A corpus: utterance → verb, slots, rung, risk, plus the must-NOT-match negatives. Append-only; every verb PR adds its rows. This file is the regression protection for the whole spec. |
| `tests/fixtures/repos/*` | ~10 project shapes for T3.3 profile detection |
| `tests/fixtures/screens/*.png` | Fixed frames for T6.3 with mocked model output |
| `tests/fakes/` | `FakeRunner` (records argv, returns scripted `Completed`), `FakeBundle`, `FakeBrain` (raises by default — the invariant-2 default) |

### 13.2 Test layering

Mirrors the existing convention (`tests/unit/<module>.py`, `tests/unit/platform/`, `tests/integration/`):

- **Unit** — everything above L5 runs against fakes. No network, no subprocess, no GUI.
- **Integration** — real sockets (T2.4 port binding), real temp git repos (T4.2), real child processes (T3.1 group-kill), a fake `codex` executable (existing pattern in `tests/unit/test_codex.py`).
- **Manual checklist per slice** — the "Acceptance" line of each gate, run on all three OSes. The wrong-workspace scenario is a standing item because it's the worst failure mode in the product.

### 13.3 CI (ROADMAP `P1-06`, currently `todo`)

This plan makes CI load-bearing rather than optional: 36 PRs across three OSes cannot be validated by hand. Minimum viable matrix — Linux runs unit + integration; macOS and Windows run unit + adapter fakes; GUI/live tests stay opt-in via a marker. Add in parallel with S1.

---

## 14. Risks

| Risk | Mitigation |
|---|---|
| **S0 changes user-visible behavior invisibly.** The refactor is the riskiest PR set in the plan because it has no new feature to test against. | Rule 1: existing tests unmodified, including the `app_action` assertion. Plus a manual pre/post script exercising all 18 browser phrases and the app/site catalogs. |
| **Latency regression from context resolution.** A focus probe or git call on every request blows the rung-1 budget (spec §12.12). | Lazy providers keyed on `verb.requires`; a rung-1 verb resolves no context. Add a timing assertion to the corpus test: rung 1–2 routing under 150 ms with fakes. |
| **The corpus becomes the spec, and drifts from it.** | The corpus file cites UC-IDs; a test asserts every UC-ID in the spec's Appendix A has at least one corpus row. |
| **Per-OS gaps get papered over by escalating to the agent.** | Invariant 5, tested: `UNSUPPORTED` never escalates rung. Windows volume and DND ship `DEGRADED` and *say so*. |
| **Rung 7 is a foot-gun.** Keystroke injection into an unknown terminal is unverifiable by construction. | Off by default behind a pack; confirm always; mandatory "not verified" caveat in every result; `terminal.repeat` refused outright. |
| **Confirm fatigue pushes users to disable it.** | R0/R1 is deliberately the large majority of verbs; the hotkey-tap path makes confirm cost ~250 ms; dry-run gives a way to inspect without a prompt. |
| **`vaani.toml` becomes a second config system.** | Repo-local overrides only for `ProjectProfile`; global phrase→argv maps stay in `commands.json` (Appendix B #8). Resolve before T3.3. |
| **Scope: 36 PRs is a long runway.** | S0–S2 is independently shippable and already covers the highest-frequency verbs. Each slice gate is a real stopping point. |

---

## 15. Decisions needed before specific tasks

From spec Appendix B, with the task each one blocks:

| # | Decision | Blocks | Recommendation |
|---|---|---|---|
| 1 | Workspace precedence | T3.2 | Take spec §5.2 as written; always report the source |
| 2 | Confirm phrase set | T2.1 | "confirm" + "do it" only. Exclude bare "yes" — too common in ordinary speech near a pending action |
| 3 | Windows volume mechanism | T1.2 | Ship `DEGRADED` in S1; revisit with pycaw once the rest of S1 is proven |
| 4 | DND on macOS/Windows | T1.2 | Accept `DEGRADED`; the hacks aren't worth the support burden |
| 5 | Wayland scope | T5.1, T6.1 | `UNSUPPORTED` for window/input; portal spike for screen only (`P0-06`) |
| 6 | Vision brain for rung 5 | T6.3 | Blocks S6 entirely — decide before S5 ends |
| 7 | Rung 7 default | T5.2 | Off until the pack is installed |
| 8 | `vaani.toml` vs `commands.json` | T3.3 | Both; repo-local wins |
| 9 | Overlay toolkit per OS | T6.2 | Reuse each pill's existing toolkit — the pill processes already exist per OS |
| 10 | Result window ↔ history UI | T1.4, T8.1 | Converge in S8; keep them separate until then |

---

## 16. ROADMAP edits (do in the first PR of each slice)

| Row | Change |
|---|---|
| `P2-01` intent router | `todo` → `in progress` at T0.4, `done` at S0 gate; link this plan |
| `P2-02` app catalog + user apps | folded into T4.1 packs |
| `P2-04` allowlisted shell | reframed: T3.1 supervisor + T3.3 profile replace a phrase→argv map for project verbs |
| `P2-05` confirmation policy | → T2.1 |
| `P2-06` agent runner | `partial` → T7.1 |
| `P2-07` focus/window targeting | → T3.2 + T5.1 |
| `P2-08` history UI | → T8.1 provides the schema it needs |
| `P2-09` custom vocabulary | → T1.1 |
| `P0-01` answer-prefix routing | fix inside T0.4 — the router's ordering (spec §1.2 step 1) is the structural fix |
| `P0-05` Codex result surface | → T1.4 |
| `P1-06` CI matrix | promote from `todo`: required by S1 onward |
| new | Add a `P2-11` row for the verb registry / capability matrix, pointing at this plan |
