# Assistant parallel agents — orchestration plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use worktree/`best-of-n-runner` local agents (not cloud). Parent merges only at slice gates. Task detail lives in [2026-07-26-assistant-verb-stack-plan.md](./2026-07-26-assistant-verb-stack-plan.md). Spec: [2026-07-26-assistant-command-use-cases.md](../specs/2026-07-26-assistant-command-use-cases.md).

**Goal:** Implement the full assistant verb stack (S0–S8) with local parallel worktree agents, maximizing shared cross-OS code, testing after every slice gate.

**Architecture:** Shared layers (`intent/`, `verbs/`, `policy/`, `exec/`, `context/`, `surface/`) own behavior; `platform/<os>/` only implements thin protocols (`SystemControl`, `WindowControl`, `InputSynth`, `TerminalOpener`, `ScreenCapture`). One registry, one `vaani do` harness, three OS leaves.

**Tech Stack:** Python 3.11+, existing Vaani PlatformBundle, pytest, local Cursor worktree agents.

---

## 0. Locked product decisions (2026-07-27)

| Topic | Decision |
|---|---|
| Scope | **Full S0–S8**, but **stop and test at each slice gate** before the next wave |
| Guide mode (S6) | **Deferred** — do not implement overlay/screen-guide now. Log deferred work in §7. Interrogatives in act mode: short refuse / “say it as a command”, no essay |
| Brains | **Pluggable adapters for Cursor, Claude, and Codex** (interface + three impls; thin stubs OK until S7 wires real exec) |
| Browser-phrase WIP | **Fold into S0** (T0.4 corpus/grammar) — keep expanded phrases |
| Agent runtime | **Local worktrees only** (no cloud agents) |
| Confirm UX | **Pill only** — show **Approve** and **Reject** on the Vaani pill. Keyboard: **Enter = Approve**, **Esc = Reject** (same Esc family as cancel). Click either control. No voice “yes/confirm” in v1 |

Confirm UX detail (overrides verb-stack plan §15 #2 and softens voice paths in T2.1):

```
[ processing / confirming phase on bottom pill ]

  [ Reject ]     waveform or status text     [ Approve ]
       │                                         │
      Esc / click                              Enter / click
```

- Pending action TTL still ~20s; new dictation utterance cancels pending (does not approve).
- Voice phrase approve is **out of v1**; can be added later without changing pill contract.
- Materialized argv/summary must be visible in the result/notify surface while pill is in `confirming`.

---

## 1. Cross-OS reuse rules (every agent must follow)

1. **Shared first:** verbs, grammar, policy, runner, supervisor, registry live under `src/vaani/` — never copy-pasted per OS.
2. **OS leaf only for syscalls:** if it shells differently (`lsof` vs `Get-NetTCPConnection`), put it behind a protocol method in `platform/<os>/`.
3. **Capability honesty:** missing OS support = `UNSUPPORTED` / `DEGRADED` with reason in `vaani caps` — never fake success or escalate to agent.
4. **One test harness:** every verb must be reachable via `vaani do … --json` with fakes; mic not required for CI.
5. **No `shell=True`**, no string-joined argv (invariants from verb-stack §13.5 / T0.7).
6. **File ownership:** agents must not edit files outside their ownership table (§3). Conflicts = rejected merge.

---

## 2. Branch / worktree naming

| Kind | Pattern | Example |
|---|---|---|
| Slice integration | `feat/av-s<N>-integrate` | `feat/av-s0-integrate` |
| Task branch | as verb-stack plan | `feat/av-router`, `plat/av-system-macos` |
| Worktree id | `av-<task>-<short>` | `av-system-macos-a1b2` |

Base for all work: `explore/assistant-use-cases` (or successor integration branch after S0 merges).

Parent workflow per slice:

1. Create `feat/av-sN-integrate` from current integration tip.
2. Launch parallel worktrees for that wave’s tasks.
3. Merge task branches → integrate branch.
4. Run **slice gate** tests (§5).
5. Only then open next wave.

---

## 3. File ownership (parallelism without fights)

| Owner agent role | May touch | Must not touch |
|---|---|---|
| **spine** | `assemble.py`, `controller.py`, `intent/*`, `verbs/registry.py`, `verbs/packs/core.py` (migration verbs only), `cli.py`, `exec/*`, `session_loop.py`, `tests/unit/intent/**`, `tests/data/utterances.yaml` (seed) | `platform/macos|windows|linux/*` adapters beyond calling `assemble` |
| **os-linux** | `platform/linux/**`, `tests/unit/platform/test_linux*` | shared verb handlers |
| **os-macos** | `platform/macos/**`, `tests/unit/platform/test_macos*` | shared verb handlers |
| **os-windows** | `platform/windows/**`, `tests/unit/platform/test_windows*` | shared verb handlers |
| **surface** | `surface/*`, `indicator_protocol.py` (phases/commands), `indicator_tk.py` / pill UX, feedback notify parity tests | OS system control impls |
| **policy** | `policy/*`, confirm engine, dry-run, undo | platform leaves |
| **packs-*** | `verbs/packs/<name>.py`, append-only corpus rows for that pack | other packs’ files |
| **brain** | `exec/agent.py`, `brains/{protocol,cursor,claude,codex}.py` | verb grammar |
| **integrate** | merge-only + ROADMAP + slice gate script | feature code except conflict fixes |

Append-only shared files (safe concurrent edits if agents coordinate at end of wave):

- `tests/data/utterances.yaml`
- `docs/install/*.md` (only the OS that agent owns)
- `ROADMAP.md` (integrate agent only)

---

## 4. Waves (local parallel agents)

### Wave 0 — S0 spine (mostly serial, 2-way parallel where noted)

| Order | Agents (parallel group) | Tasks | Notes |
|---|---|---|---|
| 0a | spine-A \|\| spine-B | **T0.1** assemble \|\| **T0.2** session loop \|\| **T0.3** contracts | Three worktrees; T0.3 has no file overlap with 0.1/0.2 |
| 0b | spine | **T0.6** runner (after 0.3) \|\| **T0.8** bundle surfaces | Parallel |
| 0c | spine | **T0.4** router + migrate today’s app/site/browser/agent + browser-phrase WIP | Serial; depends 0.3 |
| 0d | spine | **T0.5** CLI `vaani do` / `caps` | After 0.4 |
| 0e | spine | **T0.7** invariants | After 0.4 + 0.6 |
| Gate | integrate | S0 gate from verb-stack plan | Must pass before Wave 1 |

**S0 gate (must pass):**

```bash
.venv/bin/python -m pytest tests/unit -q
.venv/bin/python -m vaani do app.open --slot name=Terminal --json --dry-run
.venv/bin/python -m vaani caps --json
# Controller._process has no app/site/browser allowlist logic left
```

### Wave 1 — S1 safe system (max parallelism)

| Agents in parallel | Tasks |
|---|---|
| spine | T1.1 normalize/lexicon |
| os-linux \|\| os-macos \|\| os-windows | T1.2 `SystemControl` ×3 |
| surface | T1.4 result + pill phases (`confirming` stub OK) |
| Then spine | T1.3 core verb pack (needs T1.1 + T1.2) |

**S1 gate:** mute / volume / lock / open Terminal / open Gmail / reveal project — work or honest `DEGRADED`/`UNSUPPORTED` on each OS; `vaani caps` matches.

### Wave 2 — S2 confirm + mutating (pill UX critical)

| Agents in parallel | Tasks |
|---|---|
| policy + surface | **T2.1 confirm engine** with **pill Approve/Reject + Enter/Esc** (see §0) — **not** voice-yes |
| policy | T2.2 dry-run \|\| T2.3 undo |
| packs-core | T2.4 ports/procs/trash |
| spine | T2.5 interrogative → refuse / “say it as a command” (**not** guide-offer screen; deferred) |

**T2.1 acceptance override:**

- Pill enters `confirming` with two visible controls: Reject | Approve.
- Enter → approve; Esc → reject; click either.
- No handler runs without consumed `PendingAction`.
- Unit tests for double-approve, TTL, Esc reject, Enter approve.

**S2 gate:** `vaani do system.port.free --slot port=…` dry-run + confirm path; three OSes unit/integration as available.

### Wave 3 — S3 project & jobs

| Parallel | Tasks |
|---|---|
| packs-jobs | T3.1 supervisor |
| spine | T3.2 context (shared + os focus fakes) — OS focus probes can split os-* after protocols exist |
| spine | T3.3 project profile → T3.4 project verbs |

**S3 gate:** start/stop/restart dev server + run tests via `vaani do` on fixture repos; profile refuses when unknown.

### Wave 4 — S4 CLI packs

| Parallel | Tasks |
|---|---|
| packs-git | T4.1 packs infra → T4.2 git |
| packs-forge | T4.3 forge (after T4.2 starts / git root ready) |
| packs-pkg \|\| packs-docker | T4.4 |
| policy | T4.5 disambiguation |

**S4 gate:** branch → commit (message shown) → push → PR dry path with confirms on R3.

### Wave 5 — S5 windows/focus

| Parallel | Tasks |
|---|---|
| os-linux \|\| os-macos \|\| os-windows | T5.1 WindowControl |
| spine | T5.2 InputSynth gated + T5.3 prefer verifiable routes |

Wayland: `UNSUPPORTED` cleanly.

### Wave 6 — S6 guide (**SKIPPED / DEFERRED**)

See §7. Do not launch agents. Leave branch slot empty.

### Wave 7 — S7 agent brains

| Parallel | Tasks |
|---|---|
| brain | Protocol + **Cursor / Claude / Codex** adapters (T7.1) |
| packs-agent | T7.2 agent-backed verbs using catalog-as-tools |

Brains cannot self-approve R3/R4. Confirm still pill Enter/Esc.

### Wave 8 — S8 audit + remote

| Parallel | Tasks |
|---|---|
| spine | T8.1 history v2 |
| spine | T8.2 bridge (after T8.1 schema) |

---

## 5. Per-slice test ritual (mandatory)

After each wave’s merges, **integrate agent** runs:

```bash
.venv/bin/python -m pytest tests/unit -q
# plus slice-specific integration markers when present
.venv/bin/python -m vaani caps --json   # when CLI exists (S0+)
```

Manual (developer machine, best-effort OS):

- macOS: hold assistant chord → one happy verb from that slice → confirm on pill if R2+.
- Windows/Linux: same when available; otherwise `vaani do` + fakes count for gate.

**No Wave N+1 agents start until Wave N gate is green.**

---

## 6. Parent (you / lead agent) checklist to launch a wave

```bash
# Example Wave 1 OS trio — three local worktrees from feat/av-s1-integrate
# Each agent prompt must include:
# - task ID from verb-stack plan
# - file ownership from §3
# - reuse rules from §1
# - confirm UX from §0 (for T2.1+)
# - "run pytest for owned tests; do not modify unrelated failing tests"
```

Prompt template (paste into each worktree agent):

```
You implement ONLY task <Tx.y> from docs/superpowers/plans/2026-07-26-assistant-verb-stack-plan.md
and decisions in docs/superpowers/plans/2026-07-27-assistant-parallel-agents-plan.md §0.

Ownership: <role>. Do not edit files outside your ownership table.
Cross-OS: put logic in shared modules; OS code only behind protocols.
TDD: failing test → impl → pytest green for your tests.
Commit on branch <name>. Return: branch, commits, test output, risks.
```

---

## 7. Deferred log (Guide / S6) — do not lose this

| ID | Deferred item | Why deferred | Revisit when |
|---|---|---|---|
| D-S6 | Screen capture + overlay point/caption/tour | Product focus = commands that mutate/act; guide paused | After S5 stable; user re-opens guide |
| D-S6-offer | Interrogative → guide-offer (“I can free port 3000 — confirm?”) | Spec wanted it; user deferred guide | Can revisit as **pill offer** without screen pointer |
| D-voice-confirm | Voice “confirm”/“cancel” | Pill Enter/Esc chosen for v1 | After pill confirm feels solid |
| D-vision-brain | Vision adapter for rung 5 | Blocked on D-S6 | With S6 |

---

## 8. Mapping categories → waves (System / Terminal / CLI / UI)

| Category | Primary waves | Shared modules | OS leaves |
|---|---|---|---|
| System | S1, S2 | `verbs/packs/core.py`, `exec/runner.py` | `platform/*/system.py` |
| Terminal | S3, S5 | `exec/supervisor.py`, `verbs/packs/project.py` | `platform/*/terminal.py` |
| CLI | S4 | `verbs/packs/{git,forge,pkg,docker}.py` | almost none (argv via runner) |
| UI | S1 (browser/app), S5 (window), S7 (editor agent) | `verbs/packs/core.py`, `surface/*` | `window.py`, `input.py` |

---

## 9. Success definition for this orchestration plan

- [ ] Decisions in §0 reflected in T2.1 implementation notes (pill Approve/Reject, Enter/Esc).
- [ ] Wave 0–2 complete with gates green → daily-value assistant commands shippable.
- [ ] S6 not started; §7 deferred log intact.
- [ ] Three brain adapter modules exist by end of S7 (even if two are thin).
- [ ] No feature logic duplicated across `platform/macos|windows|linux` beyond protocol impls.

---

## 10. Immediate next step

1. Commit this plan + fold browser-phrase WIP on `explore/assistant-use-cases` (or open `feat/av-s0-integrate`).
2. Launch **Wave 0a**: three local worktrees for T0.1 / T0.2 / T0.3.
3. Merge → S0 gate → continue.
