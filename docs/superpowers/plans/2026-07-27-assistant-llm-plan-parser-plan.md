# Assistant LLM Plan Parser Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After grammar miss, use a small Groq LLM to compile natural language into a validated verb plan (with chaining), and escalate to `agent.task` only via explicit delegate or wake phrase.

**Architecture:** Keep deterministic router first. Add `catalog_card` + `intent/llm.py` that returns `IntentPlan` JSON validated against the registry. Wire `llm_parse` into the router. Add `PlanExecutor` with confirm policy B (auto R0/R1, pause on first R2+, continue after approve). Grammar corpus must still pass with LLM mocked to raise.

**Tech Stack:** Python 3.12, existing Groq chat completions (`groq.py`), verb `Registry`, `ConfirmEngine`, pytest.

**Spec:** [docs/superpowers/specs/2026-07-27-assistant-llm-plan-parser-design.md](../specs/2026-07-27-assistant-llm-plan-parser-design.md)

---

## File map

| File | Responsibility |
|---|---|
| `src/vaani/intent/schema.py` | Add `PlanStep`, `IntentPlan` (frozen dataclasses) |
| `src/vaani/intent/catalog_card.py` | Compact enabled-verb JSON for prompts |
| `src/vaani/intent/llm.py` | Prompt + parse + validate → `IntentPlan` |
| `src/vaani/intent/plan_exec.py` | Policy B execution + remaining-steps storage |
| `src/vaani/intent/router.py` | Call `llm_parse` before agent fallback; no silent agent on refuse |
| `src/vaani/groq.py` | `parse_plan_json` (or shared chat helper) |
| `src/vaani/config.py` / settings | `parse_model` optional setting |
| `src/vaani/assemble.py` / `controller.py` | Wire Groq-backed `llm_parse`; run plans via `PlanExecutor` |
| `tests/unit/intent/test_catalog_card.py` | Card shape / omissions |
| `tests/unit/intent/test_llm_parse.py` | Validation, refuse, delegate, bad JSON |
| `tests/unit/intent/test_plan_exec.py` | Policy B + continue after confirm |
| `tests/unit/intent/test_router.py` | Grammar still wins; LLM before agent; raise-mock invariant |
| `tests/data/parse_utterances.yaml` (optional v1) | NL → expected plan fixtures |

---

### Task 1: Schema — `PlanStep` / `IntentPlan`

**Files:**
- Modify: `src/vaani/intent/schema.py`
- Test: `tests/unit/intent/test_schema_plan.py`

- [ ] **Step 1: Write failing test** — construct `IntentPlan` with two steps; assert frozen / tuple steps.

- [ ] **Step 2: Add dataclasses** per design §4.1 (`PlanStep`, `IntentPlan` with `delegate_prompt` / `refuse_reason`).

- [ ] **Step 3: Run tests** — `pytest tests/unit/intent/test_schema_plan.py -q`

- [ ] **Step 4: Commit**

```bash
git add src/vaani/intent/schema.py tests/unit/intent/test_schema_plan.py
git commit -m "feat(intent): add IntentPlan contracts for LLM parse"
```

---

### Task 2: Catalog card

**Files:**
- Create: `src/vaani/intent/catalog_card.py`
- Test: `tests/unit/intent/test_catalog_card.py`

- [ ] **Step 1: Failing test** — given a tiny fake registry, card lists `site.search` with slots/risk/rung and **omits** `agent.task`.

- [ ] **Step 2: Implement** `build_catalog_card(registry, platform) -> list[dict]` (name, title, slots type map, risk, rung).

- [ ] **Step 3: pytest** green.

- [ ] **Step 4: Commit** — `feat(intent): build compact catalog card for parse LLM`

---

### Task 3: Validate + parse (pure, no network)

**Files:**
- Create: `src/vaani/intent/llm.py` (validation + `plan_from_payload` first)
- Test: `tests/unit/intent/test_llm_parse.py`

- [ ] **Step 1: Failing tests** for:
  - valid two-step plan
  - unknown verb → None / reject
  - `refuse` action
  - `delegate` action
  - max steps > 6 → reject
  - missing required slot → reject

- [ ] **Step 2: Implement** `validate_plan_payload(payload, enabled_verbs) -> IntentPlan | None`.

- [ ] **Step 3: pytest** green.

- [ ] **Step 4: Commit** — `feat(intent): validate LLM plan JSON against registry`

---

### Task 4: Groq parse helper

**Files:**
- Modify: `src/vaani/groq.py` (and settings if needed)
- Test: `tests/unit/test_groq_parse.py` with httpx mock / recorded JSON

- [ ] **Step 1: Failing test** — mock chat completion returns plan JSON; helper returns parsed dict.

- [ ] **Step 2: Add** `GroqClient.parse_intent(messages|system+user, key, *, cancel, deadline≈1.5)` returning raw text/JSON; temperature 0; extract fenced JSON like cleanup.

- [ ] **Step 3: Settings** — `parse_model: str | None = None` falling back to `cleanup_model`.

- [ ] **Step 4: Commit** — `feat(groq): add fast structured parse_intent call`

---

### Task 5: `llm_parse` callable

**Files:**
- Modify: `src/vaani/intent/llm.py`
- Test: extend `tests/unit/intent/test_llm_parse.py`

- [ ] **Step 1: Implement** `make_llm_parse(groq, key_provider, registry, platform_fn) -> Callable[[str], Intent | IntentPlan | None]`  
  Or return `IntentPlan` only; router/controller adapts.
  Preferred: **`Callable[[str], IntentPlan | None]`** so router can convert single-step to Intent and multi/delegate separately.

- [ ] **Step 2: System prompt** constant in `llm.py` (design §4.6); user message = utterance + catalog JSON + short context.

- [ ] **Step 3: Unit test** with groq stub (no network).

- [ ] **Step 4: Commit** — `feat(intent): wire Groq-backed llm_parse`

---

### Task 6: Router — LLM before agent fallback

**Files:**
- Modify: `src/vaani/intent/router.py`
- Test: `tests/unit/intent/test_router.py` (or existing router tests)

- [ ] **Step 1: Failing tests**
  - Grammar hit → `llm_parse` never called (spy).
  - Miss + LLM plan one step → Intent with `source="llm"`.
  - Miss + LLM refuse → `None` (not agent).
  - Miss + LLM delegate → `agent.task` with prompt.
  - Miss + LLM raises → `None` or refuse (not agent); invariant corpus still uses raise.

- [ ] **Step 2: Implement** after grammar miss, before current `agent.task` fallback:
  - call `self.llm_parse(utterance)` if set
  - map plan/delegate/refuse
  - **remove** blind `agent.task` fallback (or keep only behind env flag `VAANI_AGENT_FALLBACK=1` default off)

- [ ] **Step 3: Fix any tests that assumed miss → agent.task.**

- [ ] **Step 4: Commit** — `feat(intent): route grammar misses through LLM plan parser`

---

### Task 7: PlanExecutor (policy B)

**Files:**
- Create: `src/vaani/intent/plan_exec.py`
- Modify: `src/vaani/controller.py` (approve path continues rest)
- Test: `tests/unit/intent/test_plan_exec.py`

- [ ] **Step 1: Failing tests**
  - Two R0 steps → both dispatch, no confirm.
  - R0 then R2 → first runs; second stages confirm; rest stored.
  - Approve → R2 runs; any trailing R0 runs.
  - Reject → rest dropped.

- [ ] **Step 2: Implement** executor with `remaining: list[PlanStep]` on controller or ConfirmEngine side-channel (keep minimal: controller `_plan_rest`).

- [ ] **Step 3: Confirm UI detail** includes upcoming steps summary.

- [ ] **Step 4: Commit** — `feat(intent): execute multi-step plans with confirm policy B`

---

### Task 8: Assemble + history + banners

**Files:**
- Modify: `src/vaani/assemble.py`, `src/vaani/controller.py`, history write sites
- Test: smoke / controller integration with fake llm_parse

- [ ] **Step 1: Wire** real `llm_parse` in assemble when Groq+key available; inject into router.

- [ ] **Step 2: History** — each executed step writes a row; `source`/`rung` from intent; shared `raw_utterance`.

- [ ] **Step 3: Logging** — `event=llm_parse action=plan|delegate|refuse steps=N elapsed=`

- [ ] **Step 4: Commit** — `feat(assistant): enable LLM plan parser in runtime assembly`

---

### Task 9: Corpus + live gate

**Files:**
- Create or extend: `tests/data/parse_utterances.yaml` + loader test
- Manual: restart daemon on `feat/assistant-use-cases`

- [ ] **Step 1: Fixture rows** from design §6 (Search Zapto, Brave+Zepter, Thank you, fix failing test).

- [ ] **Step 2: Offline tests** use recorded LLM payloads (not live Groq in CI).

- [ ] **Step 3: Run full unit suite** — `pytest tests/unit -q`

- [ ] **Step 4: Manual checklist**
  - Grammar: `open Terminal` still instant, no parse log
  - `Search Zapto.com for me` → browser search, no agent confirm
  - `open Brave and search Zepter` → both steps
  - `Thank you` → no confirm pill
  - `Vaani, agent: …` still confirms agent
  - Esc/Enter still pass through when idle (prior fix)

- [ ] **Step 5: Commit** — `test(intent): add LLM plan utterance fixtures`

---

## Out of scope (do not do in this plan)

- Compound hardcoding in grammar for open+search
- Fixing Codex CLI install / agent empty stdout
- Local GGUF models
- Changing risk classes of existing verbs

---

## Rollback

Feature is behind “llm_parse is None” → router refuse/no-op on miss. Set parse injection off in assemble if Groq regresses.
