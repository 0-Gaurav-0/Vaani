# Assistant Skill Router Implementation Plan

> **For agentic workers:** Execute sequential checkbox tasks. Prefer TDD: write failing tests first, then minimal implementation. Keep MCP default empty.

**Spec:** `docs/superpowers/specs/2026-07-27-assistant-skill-router-design.md` (approved)

**Goal:** Route assistant voice requests through fast deterministic intents first, then a single matched Agent Skill with only its declared MCPs, else isolated Codex fallback — never boot the full MCP catalog.

**Architecture:** New `skills.py` owns index + match. Extended `codex.py` builds selective `codex exec` argv (skill prompt injection + MCP allowlist overlay). `controller.py` inserts skill match/run between site/YouTube and fallback Codex. Existing apps/sites/YouTube paths stay first.

**Tech Stack:** Python 3.13 (project venv), pytest, Codex CLI (`codex exec`), Agent Skill `SKILL.md` files under `~/.agents/skills` and `~/.claude/skills`.

---

## Global Constraints

- Assistant routing order is fixed: **app → youtube/site/browser → skill → isolated Codex**.
- Skill index reads **metadata only**; no MCP subprocess start during index/match.
- Voice MCP default is **empty**; enable only names listed on the matched skill.
- Fallback Codex keeps `--ignore-user-config` (current behavior).
- Skill runs inject one `SKILL.md` body into the prompt (v1); do not rely on loading all Codex skills.
- No Vaani-local skill authoring format in v1.
- Do not regress YouTube rickroll / app open fast paths.
- Logs: `event=assistant_route kind=skill|codex|app|browser` with skill id / mcp list; never log secrets.

---

## Task 1: Skill index + metadata parser

**Files:**
- Create: `src/vaani/skills.py`
- Create: `tests/unit/test_skills.py`
- Create: `tests/fixtures/skills/sample-skill/SKILL.md`
- Create: `tests/fixtures/skills/with-mcps/SKILL.md`

**Steps:**
- [x] Write failing tests for:
  - Parsing YAML frontmatter (`name`, `description`, `aliases`, `mcps`)
  - Scanning fixture roots into `SkillMeta` list
  - Ignoring directories without `SKILL.md`
  - Default `mcps=[]` when omitted
- [x] Implement `SkillMeta` dataclass + `load_skill_index(roots: list[Path]) -> list[SkillMeta]`
- [x] Default roots helper: `~/.agents/skills`, `~/.claude/skills` (skip missing)
- [x] Run `pytest tests/unit/test_skills.py -q` — PASS

**Done when:** Index builds from fixtures without starting any MCP/Codex process.

---

## Task 2: Skill matcher

**Files:**
- Modify: `src/vaani/skills.py`
- Modify: `tests/unit/test_skills.py`

**Steps:**
- [x] Write failing tests for:
  - Explicit: “run the sample skill …”, “use the with-mcps skill …”
  - High-confidence name match
  - Ambiguous / low-confidence → `None` (no guess)
- [x] Implement `match_skill(utterance: str, skills: list[SkillMeta]) -> SkillMeta | None`
- [x] Run `pytest tests/unit/test_skills.py -q` — PASS

**Done when:** Matcher never returns a skill on ambiguous input.

---

## Task 3: Selective Codex command builder

**Files:**
- Modify: `src/vaani/codex.py`
- Create: `tests/unit/test_codex_skill_run.py`

**Steps:**
- [x] Write failing tests for:
  - `command_for` (chat fallback) still includes `--ignore-user-config`
  - `command_for_skill(executable, prompt, mcp_names=[])` does **not** pass full user MCP set; argv contains no unrelated server names
  - With `mcp_names=["browseros"]`, argv includes config overrides that only allow that server (exact `-c` shape documented in test comments)
  - Prompt includes skill body + user utterance
- [x] Implement `CodexRunner.run_skill(skill_body: str, utterance: str, *, mcps: list[str], timeout: float | None = None)`
  - Longer default timeout than chat (e.g. 120s) — keep cancellable
  - Auth still via normal `CODEX_HOME`; do not strip auth
  - Minimal MCP overlay: prefer writing a temp config fragment or `-c` clears/adds only allowlisted servers; if Codex cannot subtract servers via `-c` alone, use `--ignore-user-config` plus explicit `-c` re-add of **only** allowlisted `mcp_servers.<name>` entries copied from `~/.codex/config.toml`
- [x] Run `pytest tests/unit/test_codex_skill_run.py tests/unit/test_sites.py -q` — PASS

**Done when:** Unit tests prove empty MCP by default and single-server allowlist when declared.

---

## Task 4: Wire controller routing

**Files:**
- Modify: `src/vaani/controller.py`
- Modify: `tests/unit/test_controller.py` (or create `tests/unit/test_controller_skills.py` if cleaner)
- Modify: Linux/macOS/Windows runtimes only if constructor needs a skill index / runner hook (prefer lazy load inside controller)

**Steps:**
- [x] Write failing tests with fakes:
  - App/youtube still short-circuit before skill
  - Matched skill calls `run_skill` with body + mcps, not `run`
  - No match → existing `codex.run`
- [x] In assistant branch after youtube/site/browser:
  - `skill = match_skill(raw, load_skill_index(...))`
  - If skill: log `event=assistant_route kind=skill id=... mcps=...`; `run_skill`; show result; history `cleanup_status=skill_action`
  - Else: existing Codex path (`kind=codex`)
- [x] On skill MCP auth failure / timeout: surface clear notification text; idle state restored
- [x] Run targeted controller + skills + codex tests — PASS

**Done when:** Routing order is enforced by tests.

---

## Task 5: Docs + manual verification

**Files:**
- Modify: `README.md` (short assistant section: fast path vs skill vs Codex)
- Optional: `ROADMAP.md` note linking this spec (only if a matching P2 row exists)

**Steps:**
- [x] Document voice examples and the MCP-empty default
- [x] Document optional skill frontmatter `mcps: [browseros]`
- [ ] Manual checklist:
  - [ ] `Ctrl+Alt+Space` → “play rickroll on YouTube” → browser opens watch URL (no Codex)
  - [ ] `Ctrl+Alt+Space` → “use the \<known skill\> …” → one skill run; process list / logs show only declared MCPs (or none)
  - [ ] Unrelated utterance → isolated Codex fallback; notification visible when widget closes
- [x] Run `pytest tests/unit/test_skills.py tests/unit/test_codex_skill_run.py tests/unit/test_sites.py tests/unit/test_apps.py -q` — PASS

**Done when:** README matches behavior; manual checklist completed.

---

## Dependency order

```
Task 1 (index) → Task 2 (match) → Task 3 (codex builder) → Task 4 (controller) → Task 5 (docs/manual)
```

Tasks 1–2 can share one PR; 3 can parallelize after 1’s `SkillMeta` shape is stable; 4 requires 2+3.

## Out of scope (do not implement in this plan)

- Vaani-local `~/.config/vaani/skills/` format
- “Which skill?” disambiguation UI
- Loading all Codex skills via user config
- Workflow learning
