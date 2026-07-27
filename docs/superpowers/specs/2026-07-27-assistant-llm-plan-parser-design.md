# Assistant LLM plan parser — design

**Status:** draft for review  
**Branch:** `feat/assistant-use-cases`  
**Date:** 2026-07-27  
**Depends on:** verb stack contracts ([assistant-command-use-cases](2026-07-26-assistant-command-use-cases.md) §4–§5), existing `llm_parse` hook in `intent/router.py`

---

## 1. Problem

Deterministic grammar + resolvers cover clear phrases (`open Terminal`, `search for Wikipedia`). Natural speech does not:

- ASR noise: `search Olaf or me`, `Killport 3000`
- Paraphrase: `Search Zapto.com for me`, `route to a projects folder`
- Compounds: `open Brave and search Zepter`
- Polite / empty: `Thank you.`

Today, grammar miss → **`agent.task`** (R2 confirm) → Codex. That is wrong for two reasons:

1. **Routing ≠ agent work.** Opening a browser or searching should never need a coding agent.
2. **Agent path is broken/noisy on this machine** (Codex CLI `ENOENT`) and returns empty success, so fallback feels like a no-op.

The ladder already reserved the missing middle rung: *grammar → LLM parse → agent* ([use-cases §13.1 L1](2026-07-26-assistant-command-use-cases.md)).

---

## 2. Goals

1. Map messy natural language onto **only verbs in the enabled registry**, with slots filled.
2. Support **multi-step plans** from one utterance (chaining) without hand-written compound rules.
3. Escalate to **`agent.task` only** when the catalog cannot express the ask (or the user used a wake phrase).
4. Keep **grammar first** — never shadow rung-1/2 deterministic hits (invariant §13.5 #2).
5. Confirm policy **B:** auto-run R0/R1 steps; pause on the first R2+ step; after approve, continue remaining steps.
6. Latency target for parse: **&lt; 500 ms** post-transcript on Groq small/instant models (best-effort; hard fail-open to agent/refuse).

### Non-goals (this slice)

- Local on-device LLM (future option; same JSON contract).
- Fixing / replacing Codex CLI (separate; agent still rung 6).
- Guide / vision (S6 deferred).
- Teaching the model free-form shell — **forbidden**. Output is structured intents only.

---

## 3. Decisions (locked)

| Decision | Choice |
|---|---|
| Model host | **Groq** chat completions (reuse API key + HTTP client patterns) |
| Model role | **Parse / plan only** — not execution |
| Confirm multi-step | **B** — run R0/R1; pause at first R2+; continue after approve |
| Agent | Only via wake phrase **or** explicit LLM `delegate` when no catalog fit |
| Grammar | Always wins when it matches; LLM not consulted |
| Invalid LLM output | Reject → treat as no-parse (optional soft `agent.task` only if confidence path says so; default: refuse with short message) |

---

## 4. Architecture

```
transcript
    │
    ▼
normalize + lexicon
    │
    ▼
router (existing)
    ├─ wake phrase ──────────────────────────────► agent.task (rung 6)
    ├─ high-priority grammar / app / site / grammar
    │         match ─────────────────────────────► single Intent (source=grammar)
    └─ miss
          │
          ▼
     llm_parse(utterance, catalog, context summary)
          │
          ├─ Plan{steps: [Intent…]} ─────────────► PlanExecutor
          ├─ Delegate{prompt} ───────────────────► agent.task (confirm)
          └─ None / Refuse ──────────────────────► short refuse / noop (no agent spam)
```

### 4.1 New types

```python
@dataclass(frozen=True)
class PlanStep:
    verb: str
    slots: Mapping[str, Any]
    note: str | None = None  # optional human hint for confirm UI

@dataclass(frozen=True)
class IntentPlan:
    """Ordered executable plan from the parse LLM (or a single grammar hit wrapped)."""
    steps: tuple[PlanStep, ...]
    utterance: str
    raw_utterance: str
    source: str                    # "llm" | "grammar"
    confidence: float
    delegate_prompt: str | None    # if set, steps empty → agent.task
    refuse_reason: str | None      # polite/empty/out-of-scope
```

Router continues to return a single `Intent` for the grammar path. The controller (or a thin `PlanExecutor`) accepts either:

- one `Intent`, or
- an `IntentPlan` from `llm_parse`.

Single grammar hits can be wrapped as a one-step plan at the controller boundary so execution is uniform.

### 4.2 Catalog card (prompt input)

Built each request from `Registry.enabled(platform)` — compact, not full handler docs:

```json
{
  "verbs": [
    {
      "name": "site.search",
      "title": "Search the web",
      "slots": {"query": "str", "browser": "str?"},
      "risk": "R0",
      "rung": 1
    }
  ]
}
```

Omit `agent.task` from the tool list shown to the parse model (delegation is a separate output channel). Cap prompt size: titles + slot names only; drop pack-internal verbs if needed later.

Optional context blurb (short): focused app name, workspace basename — never secrets, never full paths with home expansion dumps.

### 4.3 Model output (JSON only)

```json
{
  "action": "plan" | "delegate" | "refuse",
  "confidence": 0.0,
  "steps": [
    {"verb": "app.open", "slots": {"name": "Brave Browser"}},
    {"verb": "site.search", "slots": {"query": "Zepter", "browser": "brave"}}
  ],
  "delegate_prompt": null,
  "refuse_reason": null
}
```

Validation rules (hard):

1. JSON parse succeeds.
2. `action=plan` ⇒ `steps` non-empty; every `verb` ∈ enabled registry; required slots present (type-coerced lightly).
3. `action=delegate` ⇒ non-empty `delegate_prompt`; `steps` empty.
4. `action=refuse` ⇒ reason short; no steps.
5. Unknown verbs / extra keys stripped or rejected (reject whole plan).
6. Max steps: **6** (prevent runaway chains).
7. Temperature **0**; response_format JSON if Groq supports it for the chosen model, else fenced-JSON extract like cleanup.

### 4.4 PlanExecutor (confirm policy B)

For each step in order:

1. Resolve `Verb` + build `Intent` (`source="llm"`, rung from verb).
2. Policy check (risk, support, interrogative refuse — existing).
3. If risk ≤ R1 and not otherwise gated → `dispatch` immediately.
4. If risk ≥ R2 → stage **one** `PendingAction` for that step; store remaining steps on the pending bundle (or controller field `_plan_rest`).
5. On approve → execute confirmed step → continue from `_plan_rest` with the same B rules.
6. On reject / expire → drop rest; do not run later steps.
7. If any step `FAILED` / `UNSUPPORTED` → stop; surface partial summary (`Status.PARTIAL` if earlier steps ok).

Confirm UI detail should list the full plan when staging the first R2+ step (“Then: …”).

### 4.5 When to call the LLM

| Path | Call LLM? |
|---|---|
| Grammar / app / site hit | No |
| Wake phrase | No (agent) |
| Grammar miss | Yes |
| LLM timeout / error | No → refuse (“Couldn’t understand”) — **do not** auto-agent by default |
| User said “Vaani, agent: …” | Agent only |

Optional later: if `confidence < 0.55` on a plan, disambiguate or refuse instead of executing.

### 4.6 System prompt principles (short)

- You are Vaani’s **intent compiler**, not an assistant that chats.
- Emit JSON only; verbs must be from the catalog.
- Prefer the smallest plan that fulfills the utterance.
- Compounds → ordered steps (open then search).
- Polite chit-chat / thanks → `refuse` (noop), never delegate.
- Coding / multi-file / unclear how → `delegate` with a cleaned prompt.
- Never invent shell commands or verbs.

---

## 5. Module layout

| Path | Role |
|---|---|
| `src/vaani/intent/catalog_card.py` | Build compact verb JSON for the prompt |
| `src/vaani/intent/llm.py` | `parse_plan(utterance, catalog, *, key, cancel) -> IntentPlan \| None` |
| `src/vaani/intent/plan_exec.py` | `PlanExecutor` — policy B walk + rest storage |
| `src/vaani/intent/router.py` | After grammar miss, call `llm_parse` before `agent.task` fallback |
| `src/vaani/groq.py` | `parse_intent(...)` chat helper (or thin wrapper used by `llm.py`) |
| settings | `parse_model` (default: same class as cleanup / small instant model) |
| history | Record `source=llm`, step count, plan id; per-step verb rows preferred |

Router’s injected `llm_parse` becomes a real callable from assemble/controller (Groq-backed), still mockable to raise for invariant tests.

---

## 6. Examples (acceptance)

| Utterance | Expected |
|---|---|
| `Open Terminal` | Grammar → `app.open` (LLM never called) |
| `Search Zapto.com for me` | LLM → `site.search` {query: Zapto.com} |
| `open Brave and search Zepter` | LLM → `[app.open Brave, site.search Zepter]` |
| `Thank you.` | LLM → refuse/noop (no confirm) |
| `fix the failing test` | LLM → delegate → `agent.task` (confirm) |
| `Vaani, agent: refactor auth` | Wake → `agent.task` (no LLM parse) |
| `Killport 3000` | Grammar/lexicon if present; else LLM → `system.port.free` |

---

## 7. Risks & mitigations

| Risk | Mitigation |
|---|---|
| LLM invents verbs | Hard validate against registry; reject plan |
| LLM picks `agent.task` for everything | Omit from catalog card; separate `delegate` action |
| Latency spike | Short deadline (~1.5s); fail → refuse, not hang |
| Cost | Small model; grammar short-circuits majority |
| Multi-step mid-failure | Stop; `PARTIAL`; don’t continue blindly |
| Shadows grammar | Call LLM only after miss; corpus test with LLM raising |

---

## 8. Open items (defaults proposed)

1. **Default on parse failure:** refuse (not agent). ✅ proposed  
2. **Parse model default:** reuse `cleanup_model` unless a dedicated `parse_model` is set. ✅ proposed  
3. **History:** one parent row + child step rows vs single row with JSON plan — prefer **one history row per executed step**, plus `plan_id` column later if needed. v1: per-step rows with shared `raw_utterance`.  
4. **CLI:** `vaani do` stays explicit verbs; optional later `vaani understand "…" --json` for debugging parse only.

---

## 9. Success metrics

- Live phrases that currently become empty `agent.task` (`Search Zapto.com for me`, compounds) become catalog verbs / plans.
- `Thank you` no longer stages confirm.
- Rung-1/2 corpus still green with `llm_parse` mocked to raise.
- p50 parse latency under ~500 ms in manual Groq checks.
