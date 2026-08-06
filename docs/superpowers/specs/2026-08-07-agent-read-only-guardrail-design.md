# Agent read-only guardrail (Hermes / Vaani handoff)

**Date:** 2026-08-07  
**Status:** Approved for implementation  
**Scope:** Hermes agent path only (voice handoff + skill router → agent). Local Vaani actions (media, volume, open app/site, dictation paste) are unchanged.

## Goal

Prevent catastrophic or unwanted side effects when Vaani mishears a request and forwards it to Hermes. The agent behind Vaani must be **read-only**: it may inspect and report, never mutate.

## Decisions

| Decision | Choice |
|----------|--------|
| What is restricted? | Hermes / agent only (option A) |
| Explicit write requests? | Always refuse — no confirm override (option 1) |
| Local play/open/volume/paste? | Allowed (out of scope) |
| Enforcement style | Defense in depth: Vaani pre-filter + agent standing rules |

## Policy (normative)

### Allowed (read)

- List / get / search / report / summarize
- Basecamp (and similar) **read** APIs and CLIs: assigned work, todo details, project status, search
- Metabase / dashboard **queries**
- Codebase **read** for answering questions (open files, grep, git log/status/diff as inspection)
- Answering questions with no side effects

### Forbidden (write / mutate) — always refuse

Any intent or tool use that creates, updates, deletes, or sends data, including but not limited to:

- delete, remove, destroy, drop, trash
- update, edit, modify, alter, change, rename, move
- create, add, insert, append (to remote systems or files as a write)
- post, put, patch, publish
- send, email, message, notify (outbound)
- commit, push, merge, rebase, force-push
- mark done / complete, reassign, comment, close, reopen (issue trackers / Basecamp)
- file overwrite / write / apply_patch that changes the workspace
- running destructive shell (`rm`, `mv` into place, `chmod` that locks out, DB migrations, etc.)

If the user asks for a forbidden action, the agent (or Vaani gate) **refuses briefly** and may offer the read-only equivalent (“I can show that todo, but I can’t delete it”).

No voice “yes, do it” override in v1.

## Architecture

```
User utterance
    → Vaani local fast paths (media/volume/open/…)  [unchanged]
    → Agent handoff candidate
         → looks_like_agent_mutation(utterance)?
              YES → refuse in Vaani (no Hermes)
              NO  → handoff with read-only policy prefix
                     → Hermes reads AGENTS.md (same policy)
                     → mid-session: agent must still refuse writes
```

### 1. Vaani pre-filter

- New helper (e.g. `looks_like_agent_mutation(text) -> bool`) with phrase patterns for mutate verbs (EN + common Hinglish: delete/hatao/update/bhejo/… ).
- Applied on every path that calls `_assistant_codex` / `start_handoff` (explicit “ask Vaani”, router `codex`/`skill`, session resume with new follow-up).
- On hit: log `event=agent_mutation_rejected`, show short refuse text, idle — **do not** start Hermes.

False positives: prefer refusing ambiguous mutate language over letting a delete through. False negatives are mitigated by AGENTS.md.

### 2. Handoff policy prefix

When a handoff proceeds, prepend a short immutable block (similar to work-context hint), e.g.:

```text
[Vaani policy] READ ONLY. Do not delete, update, create, post, send, commit,
or otherwise mutate any system. If asked to mutate, refuse and offer read-only help.
```

### 3. Agent standing rules

Update `~/vaani-agent/vani-task/AGENTS.md` with the same policy as a top-level hard rule (before Basecamp-first defaults). Clarify Basecamp: reports/search/get only; no create/update/delete/comment/complete.

### 4. Out of scope (v1)

- OS-level sandbox / seccomp for Hermes binaries
- Per-skill CLI allowlists inside Basecamp package (document read-only usage; tighten later if needed)
- Confirm dialogs for writes (explicitly rejected for v1)
- Changing local Vaani play/open/paste behavior

## UX

- Refuse copy: short, spoken-friendly — e.g. “I can only read that — I won’t delete or change anything.”
- No clarify picker for “Confirm delete” — not offered.
- Local media mis-hears remain a separate problem (already partially guarded).

## Testing

- Unit: mutation detector hits (`delete the todo`, `update assignee`, `send this email`, `hata do`, `mark complete`) and misses (`what are my todos`, `status of this`, `summarize Basecamp`).
- Unit/controller: handoff not started when mutation detected.
- Manual: “ask Vaani delete my todo” → refuse; “ask Vaani what am I assigned” → Hermes runs read path.

## Success criteria

1. Obvious voice write requests never start Hermes.
2. Hermes project instructions state read-only and refuse writes mid-session.
3. Local Vaani media/open/volume still work without confirmation.
