# Assistant Dogfood Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the live assistant hotkey path match the visual guide+act dogfood loop — search → open first result (structured or vision+click) → unknown “open X” → search — without hardcoding site URLs or cloning HeyClicky/OpenClicky as a full agent OS.

**Architecture:** Keep Vaani’s verb compiler spine. Finish the **integrate gap** from V3 (wire `screen` / `input` / `guide_brain` into `browser.result.open` via lazy getters, same pattern as the guide pack). Add a **handler-level** unknown-`app.open` → `site.search` fallback (no new URL catalog). Surface PARTIAL/FAILED after confirm. Defer continuous agent loops, wake-phrase polish, and multi-monitor V4.

**Tech Stack:** Existing Vaani Controller / assemble, `verbs/packs/browser_results.py`, `verbs/packs/core.py`, Groq vision via `guide_brain`, macOS Screen Recording + Accessibility, pytest.

**Specs:**  
- [assistant-command-use-cases](../specs/2026-07-26-assistant-command-use-cases.md) §2 A1–A10, §4 ladder, §11 guide  
- [visual-guide-act-design](../specs/2026-07-27-visual-guide-act-design.md)  
- Prior plan (partially shipped): [visual-guide-act-plan](2026-07-27-visual-guide-act-plan.md)

**Branch:** `explore/visual-computer-use`

**Dogfood success criteria (manual):**

1. “Open Chrome and search for Blinkit” → Google SERP opens.  
2. “Open the first site” → confirm → either opens the Blinkit URL **or** vision-clicks the organic link (Screen Recording + Accessibility granted).  
3. “Open Blinkit” (no desktop app) → Google search for Blinkit (not “No app matched”).  
4. On miss/degraded: user **sees** a clear message (not silent success).  
5. No new hardcoded Blinkit/Zepto/etc URLs in `PUBLIC_SITES`.

---

## Why this plan exists (honest baseline)

| Layer | Status after V0–V3 |
|---|---|
| Vision helpers, overlay, guide pack | Shipped |
| `browser.result.open` structured + `_vision_click_fallback` | Code exists; **live deps not injected** |
| `register_browser_results_pack(registry)` | Called with **no** getters |
| `guide_brain` | Set in `assemble.py` **after** `Controller.__init__` builds core registry → must use **lazy getters** |
| Approve → PARTIAL UI | Partially fixed in working tree; must land |
| Unknown `app.open(name)` | Still fails when not in apps / `PUBLIC_SITES` |

**Out of scope for this plan:** OpenClicky continuous agent, AirPods confirm, HTTP control bridge parity, ScreenCaptureKit rewrite, Win/Linux capture, full act/guide classifier rewrite (optional Task 6 only if time).

---

## File map

| File | Responsibility |
|---|---|
| `src/vaani/verbs/packs/browser_results.py` | Accept `get_screen` / `get_input` / `get_guide_brain`; resolve brain lazily inside fallback; clearer PARTIAL evidence |
| `src/vaani/verbs/packs/core.py` | Pass getters into `register_browser_results_pack`; `app.open` miss → `site.search` |
| `src/vaani/controller.py` | Pass getters from `build_core_registry`; land approve `_surface_result` + PARTIAL cue |
| `src/vaani/cli.py` | Pass getters when available (or document dry-run-only vision) |
| `src/vaani/intent/llm.py` + `catalog_card.py` | Prompt: unknown open → `site.search` (no invent URLs) — already drafted; keep |
| `tests/unit/verbs/test_browser_result_open.py` | Live-wiring style: getters present → vision path invoked |
| `tests/unit/verbs/test_core_pack.py` | Unknown name → search URL opened |
| `tests/unit/test_controller_confirm.py` | Approve surfaces PARTIAL |
| `docs/ROADMAP.md` | One-line note: dogfood repair / live wire |

---

## Locked decisions

| Topic | Choice |
|---|---|
| Hardcoded storefront URLs | **Forbidden** for this repair |
| Unknown desktop name | Fall back to **`site.search`** with `query=<name>` |
| Vision wiring | **Lazy getters** (mirror guide pack) — not snapshot at registry build |
| Confirm for first-result | Keep single R2 confirm on the verb (already approved before handler runs) |
| Permission miss messaging | PARTIAL/FAILED detail must name Screen Recording / Accessibility when capture/click fails |
| Agent wake / session loop | **Not in this plan** |

---

### Task 0: Land approve-surface + prompt cleanup (working tree)

**Files:**
- Modify: `src/vaani/controller.py` (`_handle_approve` → `_surface_result`; PARTIAL → busy cue)
- Modify: `src/vaani/intent/llm.py`, `src/vaani/intent/catalog_card.py` (unknown open → site.search; no storefront list)
- Modify: `tests/unit/test_controller_confirm.py` (`test_approve_surfaces_partial_result`)
- Ensure: `src/vaani/sites.py` has **no** Blinkit/Zepto/Swiggy/Zomato entries

- [ ] **Step 1: Verify no storefront hardcodes**

```bash
rg -n "blinkit|zepto|swiggy|zomato" src/vaani/sites.py || true
```

Expected: no matches (or only comments — prefer zero).

- [ ] **Step 2: Confirm approve surfaces PARTIAL**

In `_handle_approve` (both single-verb and plan-continue paths), after a non-FAILED dispatch:

```python
self.logger.info(
    "event=confirm_dispatch status=%s summary=%r",
    result.status.value,
    (result.summary or "")[:160],
)
self._surface_result(result)
```

And in `_finish_assistant_result`:

```python
cue = "busy" if result.status is Status.PARTIAL else "success"
self._feedback(cue)
```

- [ ] **Step 3: Run targeted tests**

```bash
.venv/bin/python -m pytest \
  tests/unit/test_controller_confirm.py::test_approve_surfaces_partial_result \
  tests/unit/test_controller_confirm.py::test_pill_approve_and_reject_commands \
  -q
```

Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add src/vaani/controller.py src/vaani/intent/llm.py src/vaani/intent/catalog_card.py \
        tests/unit/test_controller_confirm.py
git commit -m "$(cat <<'EOF'
fix(assistant): surface confirm results and steer unknown opens to search

EOF
)"
```

---

### Task 1: Lazy getters for browser.result vision fallback

**Files:**
- Modify: `src/vaani/verbs/packs/browser_results.py`
- Modify: `src/vaani/verbs/packs/core.py` (`build_core_registry` signature + call)
- Modify: `src/vaani/controller.py` (`build_core_registry(...)` call site)
- Modify: `src/vaani/cli.py` (pass getters when constructing registry)
- Test: `tests/unit/verbs/test_browser_result_open.py`

- [ ] **Step 1: Write failing test — getters invoke vision path**

Add **only** this focused test to `tests/unit/verbs/test_browser_result_open.py` (do not leave incomplete sketches in the tree):

```python
def test_register_accepts_get_guide_brain(monkeypatch):
    from vaani.intent.schema import OverlayOp
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

    class Screen:
        pass

    class Synth:
        def click(self, x, y):
            return Result(status=Status.OK, summary="ok", detail=f"{x},{y}", rung=2)

    monkeypatch.setattr(
        "vaani.vision.capture.capture_frames",
        lambda screen: [Frame()],
    )

    verbs = build_browser_result_verbs(
        resolve_result_url=lambda _i: None,
        get_screen=lambda: Screen(),
        get_input=lambda: Synth(),
        get_guide_brain=lambda: brain,
    )
    result = verbs[0].handler(_intent(), _context())
    assert result.status is Status.OK
    assert brain_calls
    assert "vision_click" in (result.evidence or ())
```

**Constructor timing:** `Controller` / CLI may assign `self.screen` / `self.input` / `self.guide_brain` after `build_core_registry`. Late-bound `lambda: self.guide_brain` is correct; initialize attributes to `None` before the registry call if needed so the lambdas never AttributeError.
- [ ] **Step 2: Run test — expect FAIL** (no `get_guide_brain` param yet)

```bash
.venv/bin/python -m pytest \
  tests/unit/verbs/test_browser_result_open.py::test_register_accepts_get_guide_brain -v
```

Expected: FAIL (`TypeError: unexpected keyword` or brain never called)

- [ ] **Step 3: Implement lazy brain resolution**

In `browser_results.py`:

1. Add type alias `GuideBrainGetter = Callable[[], GuideBrain | None]`.
2. Change `build_browser_result_verbs` / `register_browser_results_pack` to accept `get_guide_brain` (preferred) and keep optional static `guide_brain` for tests.
3. In `_vision_click_fallback` (or handler before call):

```python
brain = get_guide_brain() if get_guide_brain is not None else guide_brain
if get_screen is None or get_input is None or brain is None:
    return None
# ... use brain instead of guide_brain
```

4. Extend `build_core_registry` with:

```python
get_screen: Callable[[], Any | None] | None = None,
get_input: Callable[[], Any | None] | None = None,
get_guide_brain: Callable[[], Any | None] | None = None,
```

and:

```python
browser_results = register_browser_results_pack(
    registry,
    get_screen=get_screen,
    get_input=get_input,
    get_guide_brain=get_guide_brain,
)
```

5. In `Controller.__init__` `build_core_registry(...)` add:

```python
get_screen=lambda: self.screen,
get_input=lambda: self.input,
get_guide_brain=lambda: self.guide_brain,
```

(`guide_brain` may be `None` until `assemble` sets it — lazy lambda is required.)

6. In `cli.py` `build_core_registry`, pass the same getters if CLI has screen/input/brain; otherwise leave `None` (honest PARTIAL).

- [ ] **Step 4: Run tests**

```bash
.venv/bin/python -m pytest tests/unit/verbs/test_browser_result_open.py -q
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/vaani/verbs/packs/browser_results.py src/vaani/verbs/packs/core.py \
        src/vaani/controller.py src/vaani/cli.py \
        tests/unit/verbs/test_browser_result_open.py
git commit -m "$(cat <<'EOF'
fix(browser): wire live screen/input/guide_brain into result.open

EOF
)"
```

---

### Task 2: Unknown app.open → site.search (no URL catalog)

**Files:**
- Modify: `src/vaani/verbs/packs/core.py` (`handle_app_open`)
- Test: `tests/unit/verbs/test_core_pack.py`

- [ ] **Step 1: Write failing tests**

```python
def test_app_open_unknown_name_falls_back_to_site_search() -> None:
    opened: list[dict] = []

    def open_browser(**kwargs):
        opened.append(kwargs)
        return "Opened browser."

    registry, _ = _registry(
        resolve_app_fn=lambda _t: None,
        resolve_site_fn=lambda _t: None,  # not in PUBLIC_SITES
        open_browser_fn=open_browser,
    )
    verb = registry.get("app.open")
    result = verb.handler(
        _intent("app.open", {"name": "Blinkit"}, utterance="Open Blinkit"),
        _context(),
    )
    assert result.status is Status.OK
    assert opened
    url = opened[0]["url"]
    assert "google.com/search" in url
    assert "Blinkit" in url or "blinkit" in url.casefold()
    assert "via=search_fallback" in (result.evidence or ())


def test_app_open_still_uses_public_site_when_listed() -> None:
    # Gmail remains catalog site.open-style fallback before search
    ...
```

Keep existing Gmail/`PUBLIC_SITES` fallback **before** search (catalog sites are intentional; storefront hardcodes are not).

Also **replace** `test_app_open_unknown_name_still_fails` (expects FAILED “No app matched”) — after this task an unknown *non-empty* name must search, not fail. Keep a FAILED case only for empty name / no utterance.

- [ ] **Step 2: Run — expect FAIL**

```bash
.venv/bin/python -m pytest \
  tests/unit/verbs/test_core_pack.py::test_app_open_unknown_name_falls_back_to_site_search -v
```

- [ ] **Step 3: Implement fallback order in `handle_app_open`**

Order:

1. `slots.target` or resolve `open {name}` / utterance as **desktop app**  
2. Else `resolve_site_fn("open {name}")` if listed in PUBLIC_SITES / local sites.json  
3. Else **`site.search` behavior**: open `https://www.google.com/search?q={quote_plus(name)}` via existing `open_browser_fn` / browser launcher  
4. Else FAILED “No app matched” only if name empty

Evidence: `("via=search_fallback", query)`.

Summary example: `Searched Google for Blinkit.`

Do **not** add Blinkit to `PUBLIC_SITES`.

- [ ] **Step 4: Run core pack tests**

```bash
.venv/bin/python -m pytest tests/unit/verbs/test_core_pack.py -q
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/vaani/verbs/packs/core.py tests/unit/verbs/test_core_pack.py
git commit -m "$(cat <<'EOF'
fix(app.open): fall back unknown names to Google search

EOF
)"
```

---

### Task 3: Honest PARTIAL reasons (permissions / missing brain)

**Files:**
- Modify: `src/vaani/verbs/packs/browser_results.py` (`handle_open` / `_vision_click_fallback`)
- Test: `tests/unit/verbs/test_browser_result_open.py`

- [ ] **Step 1: Write tests for distinct miss reasons**

Update existing `test_open_result_returns_degraded_result_when_resolver_misses` (old detail: `try guide or enable vision click`) to match the new PARTIAL copy, then add:

```python
def test_partial_when_vision_deps_missing():
    verbs = build_browser_result_verbs(resolve_result_url=lambda _i: None)
    result = verbs[0].handler(_intent(), _context())
    assert result.status is Status.PARTIAL
    assert "vision" in (result.detail or "").casefold() or "screen" in (result.detail or "").casefold()


def test_partial_when_capture_raises(monkeypatch):
    monkeypatch.setattr(
        "vaani.vision.capture.capture_frames",
        lambda screen: (_ for _ in ()).throw(RuntimeError("Screen Recording denied")),
    )
    verbs = build_browser_result_verbs(
        resolve_result_url=lambda _i: None,
        get_screen=lambda: object(),
        get_input=lambda: type("S", (), {"click": lambda self, x, y: Result(status=Status.OK, summary="x", rung=2)})(),
        get_guide_brain=lambda: (lambda q, f: ("", ())),
    )
    result = verbs[0].handler(_intent(), _context())
    assert result.status is Status.PARTIAL
    detail = (result.detail or "").casefold()
    assert "screen recording" in detail or "capture" in detail
```

- [ ] **Step 2: Implement reason strings**

Replace single generic miss with prioritized detail, e.g.:

- deps missing → `vision click unavailable (screen/input/brain not wired)`  
- capture fail → `screen capture failed — grant Screen Recording to the Vaani/Python binary and restart`  
- no POINT tags → `vision did not find result {index}`  
- click fail → return the click `Result` as-is  

Keep status `PARTIAL` (not silent OK).

- [ ] **Step 3: pytest + commit**

```bash
.venv/bin/python -m pytest tests/unit/verbs/test_browser_result_open.py -q
git add src/vaani/verbs/packs/browser_results.py tests/unit/verbs/test_browser_result_open.py
git commit -m "$(cat <<'EOF'
fix(browser): actionable PARTIAL messages for first-result misses

EOF
)"
```

---

### Task 4: Integration smoke (CLI + unit gate)

**Files:**
- Optional note: `docs/ROADMAP.md` (P2-11 / dogfood repair one-liner)
- No new production modules

- [ ] **Step 1: Full unit gate**

```bash
.venv/bin/python -m pytest tests/unit -q --tb=line
```

Expected: all previously green tests still pass (allow pre-existing skip).

- [ ] **Step 2: Dry-run verbs**

```bash
.venv/bin/python -m vaani do browser.result.open --slot index=1 --json --dry-run
.venv/bin/python -m vaani do app.open --slot name=Blinkit --json --dry-run
```

Expected: JSON shows verb + slots; dry-run does not require Screen Recording.

- [ ] **Step 3: Manual dogfood checklist** (human)

1. System Settings → Privacy → **Screen Recording** + **Accessibility** for Cursor and/or `.venv` Python; **restart** Vaani.  
2. `python -m vaani --debug`  
3. Assistant: “search Google for Blinkit” → SERP.  
4. Assistant: “open the first site” → Enter → expect open **or** visible PARTIAL with permission hint.  
5. Assistant: “open Blinkit” → Google search, not “No app matched.”

- [ ] **Step 4: ROADMAP note + commit**

```bash
git add docs/ROADMAP.md
git commit -m "$(cat <<'EOF'
docs: note assistant dogfood repair (live vision wire + search fallback)

EOF
)"
```

---

### Task 5 (optional follow-up): Minimal guide-vs-act routing

Only if Tasks 0–4 dogfood clean. **Do not start before first-result works live.**

**Files:**
- Modify: `src/vaani/intent/interrogative.py` / router mode tagging  
- Modify: `src/vaani/controller.py` (replace hard refuse with `guide.offer` when guide pack enabled)  
- Spec: use-cases §1.2  

Scope for a **later** plan: interrogative → `guide.point` / `guide.offer`; never silent `assistant runner unavailable`.

---

## Suggested improvement roadmap (beyond this plan)

Ordered by leverage for “feels like Clicky + hey-cli”:

| Priority | Item | Inspiration |
|---|---|---|
| P0 | This plan (wire vision + search fallback + surface errors) | OpenClicky visuals + hey-cli honesty |
| P1 | Harden structured SERP resolve (wait for front Chrome; clearer AX) | OpenClicky “prefer structured before CU” |
| P2 | act/guide classifier + real `guide.offer` | HeyClicky talk vs point |
| P3 | Wake phrase `"Vaani, agent:"` → rung 6 | HeyClicky agent gate |
| P4 | Session continue vs spawn for agent.task | OpenClicky sessions |
| P5 | Local overlay HTTP bridge siblings | OpenClicky `:32123` |
| P6 | Multi-display / ScreenCaptureKit | OpenClicky multi-monitor |

Do **not** jump to P3–P6 while P0 fails dogfood.

---

## Execution notes

- Prefer **subagent-driven** Task 0 → 1 → 2 → 3 → 4 serially (Task 1 unlocks live first-result).  
- Do not commit `.agents/` or unrelated SDD report noise.  
- After Task 1, if live vision still PARTIAL, fix **permissions** before rewriting capture.
