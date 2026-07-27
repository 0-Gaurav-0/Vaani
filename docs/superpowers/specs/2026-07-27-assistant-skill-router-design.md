# Assistant Skill Router (Lazy Skills + MCP)

Date: 2026-07-27  
Status: approved

## Goal

Make Vaani assistant mode feel “Google Assistant fast” for simple, known
actions, while still supporting prepared Agent Skills for real workflows —
without loading every skill and every MCP on each voice request.

## Problem

Today assistant mode is:

1. Deterministic app / site / YouTube / browser open (fast).
2. Else Codex with `--ephemeral --ignore-user-config` (slow, isolated, cannot
   use the user’s installed skills or MCPs).

Asking Codex to “play rickroll on YouTube” was slow and ineffective: Codex
could not open the desktop browser and only returned a link notification.
Asking it to run a prepared workflow skill would either fail (isolated) or,
if we naively enabled the full user config, start many MCP servers and get
worse latency.

## Decision

Use a **two-phase router** (Approach 1):

1. Keep and expand **fast deterministic** paths (no agent, no MCP).
2. For workflow requests, **match one Agent Skill from metadata only**.
3. Run the agent with **that skill’s instructions + only the MCP servers that
   skill declares** (default: zero MCPs).
4. Unmatched requests fall back to the current isolated Codex chat path.

Local Vaani-authored skill configs (`~/.config/vaani/skills/`) are **out of
scope for v1** (can be added later as path A).

## Routing pipeline (assistant mode, after STT)

Order is strict:

1. **App open** — existing `resolve_app` / platform launcher.
2. **Media / site / browser** — `resolve_youtube`, `resolve_site`,
   `_browser_intent` (including direct YouTube watch/search URLs).
3. **Skill match** — lightweight index; no MCP process start.
4. **Skill run** — selective Codex (or equivalent) invocation.
5. **Fallback** — isolated Codex chat (current flags), result shown via the
   existing assistant notification path.

Dictation modes (smart / literal) are unchanged.

## Skill index

### Sources (v1)

Scan, in order, without starting tools:

- `~/.agents/skills/*/SKILL.md`
- `~/.claude/skills/*/SKILL.md` (if present)
- Codex/plugin skill roots if a stable path is documented for this machine

### Index contents

Per skill, metadata only:

- `id` / directory name
- `name` and short `description` (from SKILL.md frontmatter when present)
- optional `aliases` (frontmatter or derived from name)
- optional `mcps: [server-name, ...]` — Codex `mcp_servers` keys to enable
- path to full `SKILL.md` body (read only when selected)

### Refresh

- Build index on Vaani assistant startup (or first assistant use).
- Optional later: voice “reload skills” — not required for v1.

### Matching

1. **Explicit** phrases win: “use/run/execute the \<name\> skill”, “with the
   \<name\> skill”.
2. Else **high-confidence** name/description match against the spoken request.
3. If ambiguous or low confidence → **do not guess**; go to fallback (or a
   short “which skill?” notification in a later iteration).

Matching must not load MCP servers and must not spawn the agent.

## Selective execution

### Hard rules

- Voice assistant **default MCP set is empty**.
- Enable MCP only from the matched skill’s `mcps` list (names must match keys
  under `[mcp_servers.*]` in the user’s Codex config).
- Never pass the full interactive Codex session config that boots all MCPs.
- Do not use `--ignore-user-config` in a way that drops auth; do use a
  **minimal overlay** (generated ephemeral profile or repeated `-c` overrides)
  so only allowlisted MCP entries exist for that run.
- Inject the selected skill body into the prompt (or Codex skill mechanism if
  we can enable **one** skill without loading all — prefer prompt injection of
  the single `SKILL.md` in v1 for predictability).

### Invocation sketch

```
codex exec
  --ephemeral
  --skip-git-repo-check   # if required for home/cwd
  -c <minimal mcp allowlist for this skill only>
  -c model_reasoning_effort="..."   # keep voice-appropriate
  "<skill body + user utterance>"
```

Exact flags are an implementation detail; the invariant is **one skill + N
declared MCPs (N often 0)**.

### Timeouts and UX

- Skill runs may take longer than app opens; keep the processing pill.
- On completion, show a concise notification (existing `ResultWindow` /
  `feedback.notify` path) so “widget closed” still leaves a visible outcome.
- On timeout/cancel, say so explicitly (same as today’s Codex timeout copy).

## Fast-path expectations

Examples that must **not** enter the skill/MCP path:

- “Open VS Code” → app launcher
- “Play rickroll on YouTube” → browser opens watch URL directly
- “Open Gmail” → site launcher

Examples that **should** enter skill match:

- “Run my product comparison skill for \<product\>”
- “Use the creator skill to compare pricing for \<product\>”

## Non-goals (v1)

- Authoring a separate Vaani skill format (path A).
- Loading all Codex skills or all MCP servers “just in case”.
- Workflow learning / watch-and-replay.
- Replacing deterministic open-app / YouTube with an agent.
- Interactive MCP OAuth prompts mid-voice (if a required MCP needs login,
  fail with a clear notification; do not hang the pill silently).

## Validation

- Unit tests: routing order (app → youtube/site → skill → fallback).
- Unit tests: skill index parses metadata without starting processes.
- Unit tests: matcher prefers explicit skill phrases; rejects ambiguity.
- Unit tests: execution builder includes only declared MCP names (empty by
  default).
- Manual: voice “play rickroll on YouTube” opens browser quickly; voice “use
  \<skill\> …” starts one skill run without booting unrelated MCPs (confirm via
  logs / process list).

## Success criteria

- Simple open/play intents remain sub-second after STT (no Codex).
- Skill workflows use prepared Agent Skill instructions.
- A skill run starts **zero** MCPs unless that skill declares some; never the
  full MCP catalog.
- User can tell whether a run succeeded from the notification text.
