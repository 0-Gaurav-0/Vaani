# Visual Guide + Act Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Local worktrees / `best-of-n-runner` OK for parallel waves; **no cloud agents**.

**Goal:** Ship Clicky-shaped **guide** (point on screen) and **act** (“open first result”) on Vaani’s existing hotkey → STT → LLM plan → verb ladder — vision/capture/overlay only where needed.

**Architecture:** Pure vision helpers (`point_parse`, `coords`, `shrink`) + `ScreenCapture` / overlay IPC leaves + installable `guide` pack + `browser.result.open` (structured first, vision+click fallback). Capture runs **only** for verbs with `requires={"screen"}` (or act vision fallback). Never write frames to disk.

**Tech Stack:** Python 3.10+, existing `ScreenFrame` / `OverlayOp` / `PlatformBundle.screen`, Groq (text plan already; vision chat for guide), Pillow (optional dep for shrink/JPEG), pytest, Tk/AppKit overlay via existing indicator process.

**Spec:** [docs/superpowers/specs/2026-07-27-visual-guide-act-design.md](../specs/2026-07-27-visual-guide-act-design.md)

**Branch:** `explore/visual-computer-use`

---

## Global constraints

1. **Vaani spine stays:** triggers, STT, Groq plan parser, ConfirmEngine policy B — do not route with vision-first.
2. **OpenClicky = visuals only:** capture shrink, `[POINT:…]` tags, coord transforms, click-through overlay. No TTS buddy clone, no cursor warp for guide.
3. **Privacy:** `ScreenFrame.data` never written to disk; history logs labels/coords only.
4. **Capability honesty:** missing capture/overlay → `UNSUPPORTED` / `DEGRADED`, never fake OK.
5. **No `shell=True`**; no silent `agent.task` for guide/act misses.
6. **TDD:** failing test → implement → green → commit per task.
7. **Grammar/plan corpus** must stay green with vision/capture mocked to raise (same invariant as brain mock).
8. **Guide never mutates** (R0). Act clicks/navigations are R2+ and always go through ConfirmEngine.
9. **File ownership** for parallel waves — see § Parallelism. Serial tasks within a wave if ownership overlaps.

---

## File map

| File | Responsibility |
|---|---|
| `src/vaani/vision/__init__.py` | Re-exports |
| `src/vaani/vision/point_parse.py` | `[POINT:…]` / `[CAPTION:…]` → `OverlayOp` + spoken text |
| `src/vaani/vision/coords.py` | Screenshot → display → global → overlay-local transforms |
| `src/vaani/vision/shrink.py` | Max-edge 1280, JPEG ~80%; in-memory only |
| `src/vaani/vision/guide_brain.py` | Vision chat → tags + summary (injectable HTTP) |
| `src/vaani/vision/capture.py` | Orchestrate `bundle.screen` + shrink + labels; define `ScreenCaptureError` |
| `src/vaani/surface/overlay.py` | Overlay IPC client (`write_overlay` / clear) + `FakeOverlay` |
| `src/vaani/indicator_protocol.py` | Overlay control-file helpers beside phase/confirm |
| `src/vaani/intent/schema.py` | Extend `ScreenFrame` with display geometry fields |
| `src/vaani/platform/protocol.py` | `InputSynth.click`; keep `ScreenCapture` |
| `src/vaani/intent/interrogative.py` | When guide pack enabled: on-screen “where/how … here” may route to `guide.offer` / grammar instead of hard refuse for R0 guide verbs |
| `src/vaani/platform/macos/screen.py` | macOS in-memory capture → `ScreenFrame` |
| `src/vaani/platform/macos/runtime.py` | Wire `screen=` |
| `src/vaani/platform/macos/input.py` | `click(x, y)` via pynput |
| `src/vaani/platform/macos/indicator_app.py` | Draw point/caption from overlay file |
| `src/vaani/platform/windows/screen.py` | Stub → `UNSUPPORTED` until V4 (honest) |
| `src/vaani/platform/linux/screen.py` | Stub → `UNSUPPORTED` until V4 |
| `src/vaani/exec/input.py` | Fake/Unsupported `click` |
| `src/vaani/verbs/packs/guide.py` | `guide.point`, `guide.offer`, `guide.last_result` |
| `src/vaani/verbs/packs/registry.py` | Register guide pack; default_enabled once V1 green (start `False`, flip in Task 10) |
| `src/vaani/verbs/packs/core.py` or small `browser_results.py` | `browser.result.open` structured path |
| `src/vaani/assemble.py` / `controller.py` / `cli.py` | Wire screen getter + guide registration |
| `pyproject.toml` | Optional `pillow` under macos/windows extras + `[vision]` |
| `tests/unit/vision/**` | Parser, coords, shrink |
| `tests/unit/verbs/test_guide_pack.py` | Guide handlers with fakes |
| `tests/unit/verbs/test_browser_result_open.py` | Structured + vision fallback |
| `tests/unit/surface/test_overlay_ipc.py` | Overlay file round-trip |

---

## Parallelism (local agents)

| Role | Owns | Must not touch |
|---|---|---|
| **vision-core** | `src/vaani/vision/**`, `tests/unit/vision/**` | packs, platform leaves |
| **schema-protocol** | `intent/schema.py` ScreenFrame fields, `platform/protocol.py` click, `exec/input.py` fakes | guide handlers |
| **os-macos** | `platform/macos/screen.py`, runtime wire, input `click`, indicator overlay draw | Linux/Windows |
| **os-stub** | `platform/windows/screen.py`, `platform/linux/screen.py` | macos |
| **surface** | `surface/overlay.py`, `indicator_protocol.py` overlay helpers | verb handlers |
| **packs-guide** | `verbs/packs/guide.py`, registry register | computer-use internals |
| **packs-act** | `browser.result.open` + tests | guide pack file |
| **integrate** | assemble/controller/cli wire, gate script, ROADMAP note | feature internals except conflicts |

**Wave order:** V0 (vision-core ∥ schema) → gate → V1 (os-macos ∥ surface ∥ packs-guide → integrate) → gate → V2 packs-act → gate → V3 (click + vision fallback) → V4 stubs optional.

---

## Locked defaults (spec §13)

| Topic | Choice |
|---|---|
| Vision provider | Groq vision-capable model via existing `groq.py` HTTP patterns; injectable client for tests |
| Overlay process | **Extend macOS indicator** via control-file IPC (no new helper binary in V1) |
| Guide pack default | `default_enabled=False` until V1 gate; then `True` on macOS-capable builds (still toggleable) |
| First-result V2 | macOS Chrome/Safari **JS via osascript** → first organic href → `site.open`; else `DEGRADED` (V3 fills gap) |
| Pillow | Optional; shrink/JPEG skipped with clear error if missing — CI tests use pre-sized RGB/JPEG fixtures or mock shrink |

---

### Task 0: Commit design doc on explore branch

**Files:**
- Add: `docs/superpowers/specs/2026-07-27-visual-guide-act-design.md`
- Add: `docs/superpowers/plans/2026-07-27-visual-guide-act-plan.md` (this file)

- [ ] **Step 1:** Ensure branch is `explore/visual-computer-use`.

- [ ] **Step 2:** Commit docs only (do not commit `.agents/` or unrelated `uv.lock` noise unless required).

```bash
git add docs/superpowers/specs/2026-07-27-visual-guide-act-design.md \
        docs/superpowers/plans/2026-07-27-visual-guide-act-plan.md
git commit -m "$(cat <<'EOF'
docs: visual guide+act design and implementation plan

EOF
)"
```

---

### Task 1: POINT / CAPTION parser (V0)

**Files:**
- Create: `src/vaani/vision/__init__.py`
- Create: `src/vaani/vision/point_parse.py`
- Test: `tests/unit/vision/test_point_parse.py`

- [ ] **Step 1: Write failing tests**

```python
from vaani.vision.point_parse import parse_vision_reply

def test_point_with_label_and_speech():
    speech, ops = parse_vision_reply(
        "it's the blue export button near the top right [POINT:1100,40:export]"
    )
    assert "export" in speech.lower() or "blue" in speech.lower()
    assert "[POINT:" not in speech
    assert len(ops) == 1
    assert ops[0].kind == "point"
    assert ops[0].x == 1100 and ops[0].y == 40
    assert ops[0].label == "export"

def test_point_none():
    speech, ops = parse_vision_reply("nothing useful here [POINT:none]")
    assert ops == ()
    assert "POINT" not in speech

def test_caption_and_screen_suffix():
    _, ops = parse_vision_reply("here [CAPTION:10,20:save dialog] [POINT:30,40:ok:screen2]")
    assert ops[0].kind == "caption" and ops[0].text == "save dialog"
    assert ops[1].kind == "point" and ops[1].label == "ok"
    # screen index carried in label suffix or OverlayOp — use label "ok" and
    # store screen in a convention: prefer OverlayOp.label without screen;
    # add optional screen via parsing into label "ok@2" OR extend later.
    # V0: parse screenN into ops[1].label remaining "ok" and put screen index
    # in a trailing note — simplest: append to OverlayOp.text as "screen:2"
    assert "2" in (ops[1].text or ops[1].label)
```

Implementer: put multi-screen index into `OverlayOp.text` as `screen:N` when `:screenN` present; keep `label` clean.

- [ ] **Step 2: Run** `pytest tests/unit/vision/test_point_parse.py -q` → FAIL (import).

- [ ] **Step 3: Implement** `parse_vision_reply(text: str) -> tuple[str, tuple[OverlayOp, ...]]` with regex:

```text
\[POINT:none\]
\[POINT:(-?\d+),(-?\d+):([^\]]+?)(?::screen(\d+))?\]
\[CAPTION:(-?\d+),(-?\d+):([^\]]+)\]
```

Strip tags from speech; collapse whitespace; empty speech OK.

- [ ] **Step 4: pytest** green.

- [ ] **Step 5: Commit** — `feat(vision): parse OpenClicky-style POINT/CAPTION tags`

---

### Task 2: Coordinate transforms (V0)

**Files:**
- Create: `src/vaani/vision/coords.py`
- Test: `tests/unit/vision/test_coords.py`

- [ ] **Step 1: Failing tests** covering:

1. Identity: screenshot == display, origin (0,0), no flip → same point.
2. Scale: shot 1280×800 → display 2560×1600, point (640,400) → (1280,800).
3. Flip Y (AppKit): after scale, `y' = display_height - y`.
4. Origin offset: add `(origin_x, origin_y)`.
5. Clamp then reject: point outside shot bounds → `None`.
6. Dual monitor: display2 origin (1920,0); point maps into global correctly.

```python
from vaani.vision.coords import DisplayGeom, screenshot_to_global

def test_scale_and_flip():
    geom = DisplayGeom(
        shot_w=1280, shot_h=800,
        display_w=2560, display_h=1600,
        origin_x=0, origin_y=0, flip_y=True,
    )
    pt = screenshot_to_global(640, 400, geom)
    assert pt == (1280, 1600 - 800)  # scaled (1280,800) then flip Y
```

- [ ] **Step 2: Implement**

```python
@dataclass(frozen=True)
class DisplayGeom:
    shot_w: int
    shot_h: int
    display_w: int
    display_h: int
    origin_x: float = 0.0
    origin_y: float = 0.0
    flip_y: bool = False

def screenshot_to_global(x: float, y: float, geom: DisplayGeom) -> tuple[float, float] | None: ...
def global_to_overlay_local(gx: float, gy: float, geom: DisplayGeom, *, nudge: tuple[float, float] = (12, 12)) -> tuple[float, float] | None: ...
```

`global_to_overlay_local`: subtract origin; apply nudge; clamp inside padding 8px; return None if outside display.

- [ ] **Step 3: pytest** green.

- [ ] **Step 4: Commit** — `feat(vision): screenshot-to-display coordinate transforms`

---

### Task 3: Shrink helper (V0)

**Files:**
- Create: `src/vaani/vision/shrink.py`
- Test: `tests/unit/vision/test_shrink.py`
- Modify: `pyproject.toml` — add `pillow` to optional `macos`, `windows`, and new `vision` extra

- [ ] **Step 1: Failing test** — RGB fixture 2000×1000 → max edge 1280; output JPEG bytes; never touches filesystem (tmpdir unused).

```python
def test_shrink_max_edge_in_memory():
    from vaani.vision.shrink import shrink_frame_bytes
    # build a tiny PNG/JPEG via Pillow in the test, then enlarge conceptually
    ...
    out, w, h = shrink_frame_bytes(src, mime="image/png", max_edge=1280, quality=80)
    assert max(w, h) <= 1280
    assert out[:2] == b"\xff\xd8"  # jpeg
```

If Pillow missing: module raises `RuntimeError("pillow required for shrink")` and test skips via `pytest.importorskip("PIL")`.

- [ ] **Step 2: Implement** `shrink_frame_bytes`.

- [ ] **Step 3: pytest** green (with pillow installed in env).

- [ ] **Step 4: Commit** — `feat(vision): in-memory screenshot shrink to max-edge JPEG`

---

### Task 4: Extend `ScreenFrame` geometry (V0/V1 prep)

**Files:**
- Modify: `src/vaani/intent/schema.py` (`ScreenFrame`)
- Test: `tests/unit/intent/test_screen_frame.py`

- [ ] **Step 1: Failing test** — construct frame with `display_width`, `display_height`, `origin_x`, `origin_y`, `flip_y`, `mime`.

- [ ] **Step 2: Extend** (keep defaults so existing callers OK):

```python
@dataclass(frozen=True)
class ScreenFrame:
    width: int
    height: int
    data: bytes | None = None
    display_index: int = 0
    mime: str = "image/jpeg"
    display_width: int = 0
    display_height: int = 0
    origin_x: float = 0.0
    origin_y: float = 0.0
    flip_y: bool = False
```

When `display_width==0`, consumers treat shot size as display size.

- [ ] **Step 3: pytest** + fix any breakage.

- [ ] **Step 4: Commit** — `feat(intent): extend ScreenFrame with display geometry`

---

### Task 5: Overlay IPC + FakeOverlay (V0)

**Files:**
- Modify: `src/vaani/indicator_protocol.py`
- Create: `src/vaani/surface/overlay.py`
- Test: `tests/unit/surface/test_overlay_ipc.py`

- [ ] **Step 1: Failing tests** — write/read/clear overlay ops via cache dir; `FakeOverlay.show` records ops; TTL field present.

Protocol (JSON file `overlay_ops` next to `indicator_phase`):

```json
{"expires_at": 123.0, "ops": [{"kind": "point", "x": 10, "y": 20, "label": "export", "text": ""}]}
```

- [ ] **Step 2: Implement** `overlay_path`, `write_overlay`, `read_overlay`, `clear_overlay`; `FakeOverlay` / `FileOverlay` in `surface/overlay.py`.

- [ ] **Step 3: pytest** green.

- [ ] **Step 4: Commit** — `feat(surface): overlay ops IPC for guide pointers`

---

### Task 6: macOS ScreenCapture leaf + stubs (V1)

**Files:**
- Create: `src/vaani/platform/macos/screen.py`
- Create: `src/vaani/platform/windows/screen.py` (honest stub)
- Create: `src/vaani/platform/linux/screen.py` (honest stub)
- Create: minimal `src/vaani/vision/capture.py` containing **only** `ScreenCaptureError` in this task (Task 7 expands orchestration)
- Modify: `src/vaani/platform/macos/runtime.py` — **both** `build_macos()` **and** `run_macos()` must set `screen=MacScreenCapture()` (there are two `PlatformBundle(...)` constructions; the live daemon uses `run_macos()`’s bundle → `assemble()`)
- Modify: windows/linux `runtime.py` — set `screen=UnsupportedScreenCapture()` (or leave `None`; prefer stub so `caps` can report honesty)
- Test: `tests/unit/platform/test_macos_screen.py` (mock grabber; no real Screen Recording in CI)

- [ ] **Step 1: Failing test** with injectable `grab_fn` returning PNG bytes + geom.

- [ ] **Step 2: Implement** `MacScreenCapture`.
  - V1 capture backend: Pillow `ImageGrab.grab` (in-memory). Spec prefers ScreenCaptureKit — treat ImageGrab as intentional V1 shortcut; **must** populate `display_width`/`height`/`flip_y` from the actual display so Retina scale does not break Task 2 transforms.
  - **Do not** write temp files. Apply `shrink` before return.
  - Prefer focused display when `display_index` default and multi-monitor geom is available (best-effort; document if ImageGrab only returns main display).

Windows/Linux stubs:

```python
class UnsupportedScreenCapture:
    def capture(self, *, display_index: int = 0) -> ScreenFrame:
        raise ScreenCaptureError("screen capture unsupported on this platform")
```

- [ ] **Step 3: Wire `screen=` in `build_macos()` and `run_macos()`.**

- [ ] **Step 4: pytest** green.

- [ ] **Step 5: Commit** — `feat(macos): in-memory ScreenCapture for guide mode`

---

### Task 7: Guide brain (vision chat) (V1)

**Files:**
- Create: `src/vaani/vision/guide_brain.py`
- Create: `src/vaani/vision/capture.py` (label + capture orchestration)
- Modify: `src/vaani/groq.py` — add `vision_chat(...)` helper if missing (multipart/image_url base64)
- Test: `tests/unit/vision/test_guide_brain.py`

- [ ] **Step 1: Failing tests** with fake client returning a canned reply containing `[POINT:…]`; assert parse + no network.

Prompt rules (short):
- Coordinate space = screenshot pixels, top-left origin.
- End with exactly one POINT or POINT:none.
- Spoken line ≤ 80 chars before the tag; casual, no markdown.

- [ ] **Step 2: Implement** `guide_point(question, frames, *, client) -> tuple[str, tuple[OverlayOp, ...]]`.

- [ ] **Step 3: pytest** green.

- [ ] **Step 4: Commit** — `feat(vision): guide brain maps question+frames to POINT ops`

---

### Task 8: Guide pack verbs (V1)

**Files:**
- Create: `src/vaani/verbs/packs/guide.py`
- Modify: `src/vaani/verbs/packs/registry.py` — `register_guide_pack` from `register_stub_packs`; keep `default_enabled=False` for now
- Modify: `register_stub_packs` to accept `get_screen`, `get_overlay`, `guide_brain` callables (all optional; missing → guide handlers return `UNSUPPORTED`)
- Modify: `src/vaani/intent/interrogative.py` — update module docstring; **R0 guide verbs are not refused** by `should_refuse_interrogative` (already true if risk is R0); add `guide.offer` path tests so “how do I free port 3000” can grammar-hit offer without staging R2
- Test: `tests/unit/verbs/test_guide_pack.py`

Verbs:

| name | risk | requires | behavior |
|---|---|---|---|
| `guide.point` | R0 | `{"screen", "focus"}` | capture focused display → brain → `screenshot_to_global` → overlay.show → `Result` with `overlay=` + ≤80-char summary |
| `guide.offer` | R0 | — | No capture required. Summarize a **suggested act verb/phrase** the user can say (e.g. port find → “say: kill the process on port 3000”). Never mutates; never stages confirm. |
| `guide.last_result` | R0 | — | re-write last overlay ops from module-level/last-session store |

- [ ] **Step 1: Failing tests** with `FakeScreen`, fake brain, `FakeOverlay`:
  - `guide.point` returns overlay ops + clears tags from summary
  - missing screen → `UNSUPPORTED`
  - `guide.offer` for “how do I free port 3000” returns OK summary containing a sayable command hint (inject optional `offer_hint_fn` for tests)
  - `guide.last_result` with no prior → soft fail summary

- [ ] **Step 2: Implement** handlers + grammar patterns:

```text
# guide.point
where(?:'s| is) (?P<target>.+)
point (?:to|at) (?P<target>.+)
(?:find|show) (?P<target>.+) on (?:the )?screen

# guide.offer (how-to without screen)
how (?:do|can|would|should) (?:i|you|we) (?P<goal>.+)
```

When pack enabled, these patterns are registered **before** agent fallback; existing exact port grammar still wins when it matches first (router order — append guide patterns after core, same as other packs).

- [ ] **Step 3: pytest** green.

- [ ] **Step 4: Commit** — `feat(guide): guide.point / offer / last_result pack`

---

### Task 9: Indicator draws overlay (V1 macOS)

**Files:**
- Modify: `src/vaani/platform/macos/indicator_app.py` (and/or a sibling overlay window module imported by it)
- Test: unit test of “parse overlay file → draw command list”; full GUI optional/manual

Spec §4.4: **one transparent fullscreen click-through overlay per display**, not markers inside the bottom pill. V1 may implement:

1. Preferred: separate fullscreen `Toplevel`/NSWindow per screen, `ignoresMouseEvents`, draw triangle/dot + caption at **overlay-local** coords from the IPC file.
2. Acceptable interim: single fullscreen overlay on the primary display only, with `DEGRADED` evidence when ops target another screen — document in Result.detail.

- [ ] **Step 1: Poll `overlay_ops` beside phase; if present and not expired, show/update overlay window(s).**

- [ ] **Step 2: Auto-clear on expiry; controller clears on new utterance (Task 10).**

- [ ] **Step 3: Unit test loader; manual dogfood note in commit body.

- [ ] **Step 4: Commit** — `feat(macos): fullscreen click-through guide overlay`

---

### Task 10: Wire assemble / controller / CLI + enable guide default (V1 gate)

**Files:**
- Modify: `src/vaani/controller.py`, `src/vaani/cli.py`, `src/vaani/assemble.py`
- Modify: `src/vaani/verbs/packs/registry.py` — after V1 unit gate, set `guide.default_enabled=True`
- Do **not** add catalog notes for `browser.result.open` here — that is Task 11
- Test: existing unit suite; guide brain mocked to raise must not break non-guide corpus

**Wiring pattern (mandatory — Controller has no `platform` today):**

1. Add Controller kwargs (same style as `input_synth`):
   - `screen_capture: Any | None = None` → `self.screen`
   - `overlay: Any | None = None` → `self.overlay` (default `FileOverlay(cache_dir)` from indicator paths)
   - optional guide brain callable stored on controller for lazy use
2. `assemble(bundle, …)` passes:
   ```python
   screen_capture=getattr(bundle, "screen", None),
   overlay=FileOverlay(Path(settings.indicator_control_path).parent),
   ```
   Extend `register_stub_packs` call inside `Controller.__init__` with lazy getters (same pattern as `get_input=lambda: self.input`):
   ```python
   get_screen=lambda: self.screen,
   get_overlay=lambda: self.overlay,
   get_guide_brain=lambda: getattr(self, "guide_brain", None),
   ```
   Lazy getters are required because pack registration runs inside `__init__` **before** assemble finishes assigning attrs — set `self.screen = screen_capture` etc. **before** `register_stub_packs`.
3. **CLI** (`cli.py` `_build_registry` / `vaani do`): when building registry for `do`, pass `get_screen` from OS bundle if available; handlers must tolerate missing capture (`UNSUPPORTED` / dry-run). Gate:
   ```bash
   .venv/bin/python -m vaani do guide.point --slot target="address bar" --json --dry-run
   ```
   Expected: JSON result OK/UNSUPPORTED/DEGRADED — never traceback.
4. **Overlay lifecycle in Controller:**
   - After dispatch, if `result.overlay`: `self.overlay.show(result.overlay, ttl=8.0)`
   - On new `trigger()` / cancel / successful non-guide dispatch: `self.overlay.clear()`

- [ ] **Step 1:** Implement Controller + assemble wiring as above.

- [ ] **Step 2:** CLI dry-run path + caps shows guide pack.

- [ ] **Step 3: Gate commands:**

```bash
.venv/bin/python -m pytest tests/unit/vision tests/unit/surface/test_overlay_ipc.py tests/unit/verbs/test_guide_pack.py -q
.venv/bin/python -m pytest tests/unit -q
.venv/bin/python -m vaani caps --json   # includes guide row
.venv/bin/python -m vaani do guide.point --slot target="address bar" --json --dry-run
```

- [ ] **Step 4: Commit** — `feat(guide): wire guide pack into controller and caps`

**V1 gate:** unit green; CLI dry-run no crash; dogfood optional with Screen Recording.

---

### Task 11: `browser.result.open` structured (V2)

**Files:**
- Create: `src/vaani/verbs/packs/browser_results.py` (or extend `core.py` if small — prefer new file to avoid core fights)
- Register from core always-on **or** as part of core browser family (always on — this is not computer-use)
- Test: `tests/unit/verbs/test_browser_result_open.py`
- Grammar + catalog note for “open the first (result|site|link)”

- [ ] **Step 1: Failing tests**

```python
def test_open_first_result_uses_resolver_url():
    # inject resolver returning https://en.wikipedia.org/wiki/Ramayana
    # expect site.open semantics / OK with evidence url
```

- [ ] **Step 2: Implement** verb `browser.result.open` risk R2, slots `index:int=1`.
  - ConfirmEngine path: return `pending` like other R2 verbs.
  - macOS resolver: osascript JS in front Chrome/Safari to pick Nth organic result href (Google `#search a[href^="http"]` heuristic — document fragility).
  - Inject `resolve_result_url(index) -> str | None` for tests.
  - On miss → `DEGRADED` summary “couldn't read results; try guide or enable vision click” (V3).

- [ ] **Step 3: Catalog / LLM notes** in `catalog_card.py` `platform_notes`:
  - on-screen where/what → prefer `guide.point`
  - “open the first (result|site|link)” / ASR “first side” → `browser.result.open`, **not** `site.search`

- [ ] **Step 4: pytest** green + commit — `feat(browser): browser.result.open structured first-result`

**V2 gate:** unit tests with fake resolver; live dogfood after Google search optional.

---

### Task 12: InputSynth.click + vision click fallback (V3)

**Files:**
- Modify: `src/vaani/platform/protocol.py` — add `click(self, x: float, y: float) -> Result`
- Modify: `src/vaani/exec/input.py` fakes
- Modify: `src/vaani/platform/macos/input.py` (+ win/linux best-effort or UNSUPPORTED)
- Modify: `browser_results.py` — if structured miss and screen+brain available: POINT → confirm → `click` at **global** coords
- Test: extend `test_browser_result_open.py` + `tests/unit/platform/test_macos_input_click.py`

- [ ] **Step 1: Failing tests** for click recording / vision fallback pending confirm.

- [ ] **Step 2: Implement** click + fallback; **never** auto-click without confirm.

- [ ] **Step 3: pytest** green.

- [ ] **Step 4: Commit** — `feat(browser): vision+click fallback for first-result`

**V3 gate:** fake path opens via click evidence; live dogfood with confirm pill.

---

### Task 13: Caps / ROADMAP note + V4 stubs only

**Files:**
- Modify: `ROADMAP.md` — mark S6 guide in progress / done-for-macOS V1
- Windows/Linux screen already stubbed in Task 6
- Commit — `docs: note visual guide+act V0–V3 status`

No full Win/Linux overlay in this plan (V4 later).

---

## Slice gates (commands)

```bash
# V0
.venv/bin/python -m pytest tests/unit/vision tests/unit/surface/test_overlay_ipc.py -q

# V1
.venv/bin/python -m pytest tests/unit/verbs/test_guide_pack.py tests/unit/platform/test_macos_screen.py -q
.venv/bin/python -m pytest tests/unit -q

# V2–V3
.venv/bin/python -m pytest tests/unit/verbs/test_browser_result_open.py -q
.venv/bin/python -m pytest tests/unit -q
```

---

## Out of scope (do not implement in this plan)

- Continuous screen watching
- OpenClicky TTS personality / Cloudflare proxy
- CDP pack (`browser-cdp`) — later enhancement for V2 resolver
- Wayland capture
- `guide.tour` multi-marker
- Warping real cursor for guide pointing
