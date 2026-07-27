# OpenClicky-Informed Assistant Repair Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix live assistant dogfood (search → open first result → unknown “open X”) by applying OpenClicky’s **visuals + routing discipline** on Vaani’s verb spine — without cloning the Swift buddy, Agent Mode, or cua-driver MCP.

**Architecture:** Keep hotkey → STT → grammar/LLM plan → ConfirmEngine. Steal OpenClicky’s late-bound capture deps, screenshot→global map, proxy overlay (no cursor warp for guide), and “structured before pixels / CU last.” Wire existing `_vision_click_fallback` with lazy getters; unknown `app.open` → `site.search` (no storefront URL catalog). Optional one re-observe after click (Clicky snapshot→act→verify), not a continuous agent loop.

**Tech Stack:** Vaani Controller/assemble, `browser_results` / `core` packs, `vision/*`, Groq vision brain, macOS Screen Recording + Accessibility, pytest.

**Specs / notes:**
- [openclicky-architecture-lessons](../specs/2026-07-27-openclicky-architecture-lessons.md) ← **read first** (clone may be deleted)
- [visual-guide-act-design](../specs/2026-07-27-visual-guide-act-design.md)
- [assistant-command-use-cases](../specs/2026-07-26-assistant-command-use-cases.md) §2 A1–A10, §4, §11
- Supersedes day-to-day work of: [assistant-dogfood-repair-plan](2026-07-27-assistant-dogfood-repair-plan.md) (same P0 tasks; this plan adds OpenClicky-grounded rationale + Wave B)

**Temp clone (optional line-level reading):** `/Users/shubham/Desktop/Projects/_tmp/openclicky`  
Delete after implementers no longer need Swift greps: `rm -rf /Users/shubham/Desktop/Projects/_tmp/openclicky`

**Branch:** `explore/visual-computer-use`

---

## Dogfood success (definition of done)

| # | Utterance | Expected |
|---|---|---|
| 1 | “Open Chrome and search for Blinkit” | Google SERP |
| 2 | “Open the first site” + Enter | Opens organic result URL **or** vision-clicks it |
| 3 | “Open Blinkit” | Google search for Blinkit — **never** “No app matched”, **never** hardcoded blinkit.com |
| 4 | Permission / vision miss | Visible PARTIAL with Screen Recording / Accessibility hint |
| 5 | Guide “where is the address bar?” | Overlay point; system cursor **not** warped |

---

## OpenClicky → Vaani task mapping

| OpenClicky lesson | Vaani task |
|---|---|
| Late-bound capture at turn time | Task 1 lazy getters |
| Prefer NSWorkspace/open/search before CU | Task 2 search fallback |
| Proxy point ≠ click | Already (guide vs act); keep separate |
| Snapshot → act → re-snapshot | Task 4 optional verify |
| Affirmative confirm UX | Task 0 surface results |
| cua-driver / Agent Mode / `:32123` | **Out of scope** (Wave C later) |

---

## File map

| File | Responsibility |
|---|---|
| `docs/superpowers/specs/2026-07-27-openclicky-architecture-lessons.md` | Architecture teach notes |
| `src/vaani/verbs/packs/browser_results.py` | `get_guide_brain` + honest PARTIAL; optional verify |
| `src/vaani/verbs/packs/core.py` | Pass getters; `app.open` → search fallback |
| `src/vaani/controller.py` | Getters into `build_core_registry`; approve `_surface_result` |
| `src/vaani/cli.py` | Same getters when available |
| `src/vaani/intent/llm.py` / `catalog_card.py` | Unknown open → `site.search` |
| `tests/unit/verbs/test_browser_result_open.py` | Getter + PARTIAL tests |
| `tests/unit/verbs/test_core_pack.py` | Search fallback tests |
| `tests/unit/test_controller_confirm.py` | Approve surfaces PARTIAL |
| `docs/ROADMAP.md` | Short status note |

---

## Locked decisions

| Topic | Choice |
|---|---|
| Product spine | Vaani verbs + confirm (not OpenClicky Agent Mode) |
| Storefront URLs | **Never** add Blinkit/Zepto/etc to `PUBLIC_SITES` for this work |
| Unknown desktop name | `site.search` with spoken name |
| Vision deps | Lazy getters (`lambda: self.guide_brain`) — assemble sets brain after `__init__` |
| Guide cursor | Never warp system pointer for guide |
| Act click | Only after R2 confirm; may warp/click via `InputSynth.click` |
| Continuous Clicky loop | **No** in Wave A/B |
| Delete temp clone | After Wave A green or when notes suffice |

---

# Wave A — Dogfood unblock (ship first)

### Task 0: Commit architecture notes + land approve-surface

**Files:**
- Add: `docs/superpowers/specs/2026-07-27-openclicky-architecture-lessons.md`
- Add: `docs/superpowers/plans/2026-07-27-openclicky-informed-assistant-repair-plan.md` (this file)
- Modify: `src/vaani/controller.py`, `intent/llm.py`, `intent/catalog_card.py`
- Modify: `tests/unit/test_controller_confirm.py`
- Ensure: no Blinkit/Zepto hardcodes in `sites.py`

- [ ] **Step 1: Guard against storefront hardcodes**

```bash
rg -ni "blinkit|zepto|swiggy|zomato" src/vaani/sites.py || true
```

Expected: no matches.

- [ ] **Step 2: Approve path must surface Result**

In `_handle_approve` (single + plan-continue), after successful dispatch (not FAILED):

```python
self.logger.info(
    "event=confirm_dispatch status=%s summary=%r",
    result.status.value,
    (result.summary or "")[:160],
)
self._surface_result(result)
```

PARTIAL feedback cue: `"busy"` not `"success"`.

- [ ] **Step 3: Prompt notes**

Keep / ensure PARSE + platform_notes: unknown `open <name>` → `site.search`; never invent URLs; `app.open` only for desktop apps.

- [ ] **Step 4: Tests**

```bash
.venv/bin/python -m pytest \
  tests/unit/test_controller_confirm.py::test_approve_surfaces_partial_result \
  tests/unit/test_controller_confirm.py::test_pill_approve_and_reject_commands -q
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/specs/2026-07-27-openclicky-architecture-lessons.md \
        docs/superpowers/plans/2026-07-27-openclicky-informed-assistant-repair-plan.md \
        src/vaani/controller.py src/vaani/intent/llm.py src/vaani/intent/catalog_card.py \
        tests/unit/test_controller_confirm.py
git commit -m "$(cat <<'EOF'
docs+fix: OpenClicky lessons and surface confirm results

EOF
)"
```

---

### Task 1: Wire OpenClicky-style late-bound vision into `browser.result.open`

**OpenClicky parallel:** capture + brain resolved at **turn time**, not app boot. Guide pack already uses getters; browser_results does not.

**Files:**
- Modify: `src/vaani/verbs/packs/browser_results.py`
- Modify: `src/vaani/verbs/packs/core.py` (`build_core_registry`)
- Modify: `src/vaani/controller.py`, `src/vaani/cli.py`
- Test: `tests/unit/verbs/test_browser_result_open.py`

- [ ] **Step 1: Failing test**

```python
def test_register_accepts_get_guide_brain(monkeypatch):
    from vaani.intent.schema import OverlayOp, Result, Status
    from vaani.verbs.packs.browser_results import build_browser_result_verbs

    brain_calls = []

    def brain(q, frames):
        brain_calls.append(q)
        return "x", (OverlayOp(kind="point", x=5, y=5, label="1"),)

    class Frame:
        width = height = 200
        scale = 1.0
        origin_x = origin_y = 0.0
        flip_y = False

    class Synth:
        def click(self, x, y):
            return Result(status=Status.OK, summary="ok", detail=f"{x},{y}", rung=2)

    monkeypatch.setattr(
        "vaani.vision.capture.capture_frames",
        lambda screen: [Frame()],
    )

    verbs = build_browser_result_verbs(
        resolve_result_url=lambda _i: None,
        get_screen=lambda: object(),
        get_input=lambda: Synth(),
        get_guide_brain=lambda: brain,
    )
    result = verbs[0].handler(_intent(), _context())
    assert result.status is Status.OK
    assert brain_calls
    assert "vision_click" in (result.evidence or ())
```

- [ ] **Step 2: Run — expect FAIL**

```bash
.venv/bin/python -m pytest \
  tests/unit/verbs/test_browser_result_open.py::test_register_accepts_get_guide_brain -v
```

- [ ] **Step 3: Implement**

1. Add `get_guide_brain: Callable[[], GuideBrain | None] | None` to `build_browser_result_verbs` **and** `register_browser_results_pack` (same signature surface).
2. Resolve brain at call time inside `_vision_click_fallback` / handler:

```python
brain = get_guide_brain() if get_guide_brain is not None else guide_brain
if get_screen is None or get_input is None or brain is None:
    return None  # caller turns into actionable PARTIAL
```

3. **Critical live wire** — in `build_core_registry`, change the bare call to pass getters through (this is the dogfood bug):

```python
# BEFORE (broken live):
# browser_results = register_browser_results_pack(registry)

# AFTER:
browser_results = register_browser_results_pack(
    registry,
    get_screen=get_screen,
    get_input=get_input,
    get_guide_brain=get_guide_brain,
)
```

Add matching optional params on `build_core_registry` itself.

4. Controller `build_core_registry(...)`:

```python
get_screen=lambda: self.screen,
get_input=lambda: self.input,
get_guide_brain=lambda: self.guide_brain,
```

Initialize `self.guide_brain = None` (and ensure `self.screen` / `self.input` exist) **before** `build_core_registry` so lambdas never AttributeError. `assemble.py` still assigns the real brain after `__init__`.

5. **Keep** the same getters on `register_stub_packs(...)` for the **guide** pack — do not move them; guide and browser_results both need late-bound access.

6. CLI: create `screen = None` / `input_synth = None` / `guide_brain = None` locals (or attrs), pass `lambda: screen` etc. into `build_core_registry`, **then** assign real objects — same late-bound pattern. Do not require objects to “exist” before the registry call.

7. Add an integration-style unit test that fails if registry registration drops getters — e.g. build via `build_core_registry(..., get_guide_brain=lambda: brain, ...)` with `resolve_result_url=...` injected if needed, **or** monkeypatch `register_browser_results_pack` to assert kwargs were forwarded. Minimum: after Step 3, `rg "register_browser_results_pack" src/vaani/verbs/packs/core.py` must show getter kwargs.

- [ ] **Step 4: pytest**

```bash
.venv/bin/python -m pytest tests/unit/verbs/test_browser_result_open.py -q
```

- [ ] **Step 5: Commit**

```bash
git commit -m "$(cat <<'EOF'
fix(browser): late-bound screen/input/brain for first-result vision

EOF
)"
```

---

### Task 2: Unknown `app.open` → `site.search` (Clicky: don’t invent URLs)

**OpenClicky parallel:** unresolved app candidates are skipped / deferred — never fake a storefront hostname.

**Files:**
- Modify: `src/vaani/verbs/packs/core.py` (`handle_app_open`)
- Test: `tests/unit/verbs/test_core_pack.py`

- [ ] **Step 1: Failing tests**

```python
def test_app_open_unknown_name_falls_back_to_site_search() -> None:
    opened: list[dict] = []

    def open_browser(**kwargs):
        opened.append(kwargs)
        return "Opened browser."

    registry, _ = _registry(
        resolve_app_fn=lambda _t: None,
        resolve_site_fn=lambda _t: None,
        open_browser_fn=open_browser,
    )
    result = registry.get("app.open").handler(
        _intent("app.open", {"name": "Blinkit"}, utterance="Open Blinkit"),
        _context(),
    )
    assert result.status is Status.OK
    assert "google.com/search" in opened[0]["url"]
    assert "via=search_fallback" in (result.evidence or ())
```

**Replace** `test_app_open_unknown_name_still_fails` — empty name may still FAIL; non-empty unknown must search.

Keep Gmail/`PUBLIC_SITES` resolve **before** search.

- [ ] **Step 2–4: Implement order** app resolve → site catalog → Google search URL → else FAIL; pytest; commit

```bash
git commit -m "$(cat <<'EOF'
fix(app.open): fall back unknown names to Google search

EOF
)"
```

---

### Task 3: Actionable PARTIAL (permissions)

**OpenClicky parallel:** Settings deep-links + sticky Screen Recording preflight messaging (`WindowPositionManager.swift`).

**Files:** `browser_results.py` + tests

- [ ] Distinct details:
  - deps missing → `vision click unavailable (screen/input/brain not wired)`
  - capture error → mention **Screen Recording** + restart Vaani/Python
  - no POINT → `vision did not find result {n}`
  - click fail → return click Result
- [ ] Update old degraded-string assertions (`try guide or enable vision click`)
- [ ] Commit

```bash
git commit -m "$(cat <<'EOF'
fix(browser): actionable PARTIAL messages for first-result misses

EOF
)"
```

---

### Task 4 (optional in Wave A): One re-observe after vision click

**OpenClicky parallel:** cua-driver “snapshot before/after action” — **one** verify only.

**Files:** `browser_results.py`

- [ ] After successful vision click, optional second capture + cheap check is **out of band** if too heavy; minimum: log `event=browser_result_vision_click` with coords.
- [ ] Prefer skip if it risks flaky CI; do not block Wave A ship.

If implemented: evidence includes `verified=skip|ok|unknown`.

---

### Task 5: Gate + manual dogfood

- [ ] `pytest tests/unit -q`
- [ ] Dry-run:

```bash
.venv/bin/python -m vaani do browser.result.open --slot index=1 --json --dry-run
.venv/bin/python -m vaani do app.open --slot name=Blinkit --json --dry-run
```

- [ ] Human: grant Screen Recording + Accessibility to Cursor **and/or** `.venv` Python; **Cmd+Q restart**; run `python -m vaani --debug`; execute dogfood table above.
- [ ] ROADMAP one-liner; commit docs.
- [ ] **Delete temp clone** when no longer needed:

```bash
rm -rf /Users/shubham/Desktop/Projects/_tmp/openclicky
```

---

# Wave B — Guide/act parity (after Wave A dogfoods)

Only start after Wave A manual checklist passes.

| Task | Steal from OpenClicky | Vaani work |
|---|---|---|
| B1 | Voice ladder: affirmatives / refuse → offer | Minimal act/guide classifier; interrogative → `guide.offer` not hard refuse |
| B2 | Proxy overlay polish | Ensure guide never warps cursor; caption TTL |
| B3 | `:screenN` when multi-frame | Enforce screen index in `point_parse` + guide_brain prompt |
| B4 | Capture prewarm | Optional SCKit / shareable-content cache (macOS) — new spike plan |

---

# Wave C — Explicitly later (do not sneak into Wave A)

| Item | Why later |
|---|---|
| cua-driver MCP / full CU loop | Heavy; Vaani CU pack is keystroke macros |
| Codex Agent Mode / sessions_spawn | Wake-phrase product slice |
| `:32123` HTTP bridge | Spec A7; not Blinkit-blocking |
| RECT/SCRIBBLE tours | GUIDE-04 |
| Continuous tutor / speculative pre-fire | Latency + privacy |

---

## Execution notes

- Implement **Wave A Tasks 0→1→2→3→5** serially (Task 1 unblocks first-result).
- Prefer subagent-driven development with review between tasks.
- Do not commit `.agents/` or the `_tmp/openclicky` tree into Vaani.
- If live vision still PARTIAL after Task 1: fix **permissions + restart** before rewriting capture.
