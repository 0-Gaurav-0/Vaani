# Vaani visual guide + act — design

**Status:** draft for review  
**Branch:** `explore/visual-computer-use`  
**Date:** 2026-07-27  
**Approach:** Vaani ladder + dual visual layer (approved)  
**Inspiration:** [OpenClicky](https://github.com/jasonkneen/openclicky) / upstream Clicky — **capture, coordinates, overlay only**  
**Depends on:** [assistant-command-use-cases](2026-07-26-assistant-command-use-cases.md) §4, §11; [LLM plan parser](2026-07-27-assistant-llm-plan-parser-design.md); existing `OverlayOp`, `ScreenFrame`, `ScreenCapture` protocol

---

## 1. Problem

Vaani can already **plan and run typed verbs** (open Chrome, search Ramayana) without looking at the screen. Users still need Clicky-class visuals for:

| Ask | Need |
|---|---|
| “Where’s the export button?” / “How do I turn off notifications here?” | **Guide** — point at UI, speak one line, **no mutation** |
| “Open the first site / first result” | **Act** — open or click that result (**mutation**, confirm) |

OpenClicky solves vision well (screenshot → `[POINT:…]` → overlay triangle). It does **not** own Vaani’s strengths: verb registry, confirm/dry-run/undo, Groq plan compiler, cross-OS packs.

**We keep Vaani triggers + LLM planning. We steal OpenClicky’s visual machinery.**

---

## 2. Goals

1. **Guide (rung 5):** hotkey-gated capture → vision → overlay point/caption; never warps the system cursor; never writes frames to disk.
2. **Act (rung 7, gated):** “first result / open that” executes with **ConfirmEngine** (policy B); prefer non-vision when possible.
3. **Fast by default:** grammar → LLM plan → verbs **without** screenshots. Vision only when the plan/router selects a guide/act visual verb.
4. **Privacy:** capture only on the same assistant utterance that needs screen; in-memory `ScreenFrame` only; history logs question + verb + coords, never pixels.
5. **Cross-OS honesty:** macOS first for overlay polish; Windows/Linux capture+overlay with explicit `DEGRADED`/`UNSUPPORTED` (esp. Wayland).

### Non-goals (this design)

- Cloning OpenClicky’s TTS buddy personality, menu-bar Swift app, or Cloudflare key proxy.
- Continuous screen watching.
- Replacing the Groq plan parser with “vision-first routing.”
- Full general computer-use agent as the default path.

---

## 3. Decisions (locked)

| Decision | Choice |
|---|---|
| Product spine | **Vaani** hotkeys, STT, LLM plan, verb registry, confirm |
| Visual technique source | **OpenClicky/Clicky** capture resize, POINT tags, coord transforms, click-through overlay |
| When to capture | Only if routed verb `requires={"screen"}` (or act fallback needs vision) |
| Guide mutation | **Never** — point/caption/tour only (R0) |
| Act mutation | **Always confirm** (R2+) unless a future opt-in says otherwise |
| First-result strategy | Prefer **structured/AX/CDP/URL** before vision+click |
| Overlay | Separate process/window per display; ignore mouse events; do not move real pointer for guide |

---

## 4. What we take from OpenClicky (visuals only)

### 4.1 Capture pipeline

Adopted from Clicky/OpenClicky writeups + README:

1. On demand (not continuous): capture each display (or focused display first).
2. Downscale so **max dimension ≤ 1280**; JPEG/WebP quality ~80% — enough for UI text, cheaper tokens.
3. Label each image for the model, e.g.  
   `screen 1 of 2 — cursor/focus on this screen (primary) (1280×831)`  
   Prioritize the display that has the focused app / pointer.
4. Strip Vaani’s own overlay/pill windows from the capture when possible.
5. Hold bytes in `ScreenFrame` — **never** write to disk; drop after the turn.

Vaani already has `ScreenCapture` on `PlatformBundle` and `ScreenFrame` in schema — implement leaves:

| OS | Capture |
|---|---|
| macOS | ScreenCaptureKit (preferred) / CGWindowList fallback |
| Windows | Windows.Graphics.Capture |
| Linux | X11 shm / PipeWire portal; Wayland without portal → `UNSUPPORTED` |

### 4.2 Vision output contract (OpenClicky-shaped)

Guide vision returns **short speech text + structured overlay ops**, not free-form essays.

Preferred machine tags (parse with regex, same idea as Clicky):

```text
[POINT:x,y:label]
[POINT:x,y:label:screenN]
[POINT:none]
[CAPTION:x,y:short text]
```

Vaani maps tags → existing `OverlayOp(kind, x, y, label, text)`.

Coordinate space in the prompt: **screenshot pixels, origin top-left, +x right, +y down**, dimensions taken from the image label.

### 4.3 Coordinate transforms (the hard part)

Pipeline (same three disagreements Clicky documents):

1. Clamp `(x,y)` to screenshot bounds.
2. Scale screenshot → display pixel size; **flip Y** if the OS display space is bottom-left (AppKit).
3. Add display origin offset on the global multi-monitor grid.
4. Map global → per-overlay window local coords; optional nudge so the marker sits **beside** the target, not on top of text.
5. Reject out-of-bounds / wrong-screen ops.

Unit-test this transform table with fixtures (Retina 2×, 125% Windows, dual monitor).

### 4.4 Overlay window

OpenClicky pattern:

- One transparent fullscreen overlay **per display**
- Click-through (`ignoresMouseEvents` / equivalent)
- Float above normal windows; join all Spaces if the OS allows
- Draw Vaani marker (not necessarily a blue triangle — brand later) + short caption
- Auto-dismiss: TTL + Esc / new utterance
- Guide path **does not** move the real mouse

Vaani implementation sketch: small native helper or existing indicator process extended with overlay IPC (control file / localhost bridge sibling to `vaani bridge`).

### 4.5 What we do **not** copy

- Vision as the primary router for “open Terminal”
- Always-on companion TTS personality
- Bundled Codex-as-product-core for every ask
- Warping the system cursor for pointing

---

## 5. Vaani control flow (ours)

```
hotkey release → STT
       │
       ▼
normalize + grammar + LLM plan   ← already shipped
       │
       ├─ catalog verbs (app/site/system/…)  → PlanExecutor (no screen)
       ├─ guide.*                            → capture → vision → overlay  (rung 5)
       ├─ browser.result.open / ui.click.*   → structured first, else vision+click (rung 7, confirm)
       └─ agent.task                         → wake / explicit delegate only
```

The **plan LLM** may emit `guide.point` or `browser.result.open` the same way it emits `site.search`. It does not receive screenshots. Only the guide/act handlers request `ScreenFrame`.

Classifier additions (plan prompt + grammar):

- Interrogative + “here/this/on screen” → prefer `guide.point`
- “open the first (result|site|link)” after search context → `browser.result.open` {index:1}
- Do **not** map that utterance to `site.search` for query `"first side"`

---

## 6. Verbs

### 6.1 Guide pack (`guide`, installable, default on once stable)

| Verb | Risk | Requires | Behavior |
|---|---|---|---|
| `guide.point` | R0 | `screen`, `focus` | Capture → vision → `OverlayOp` point + ≤80-char summary |
| `guide.offer` | R0 | context as needed | Finding + offer act verb (existing interrogative refuse path grows up) |
| `guide.last_result` | R0 | — | Re-show last overlay/summary |

Deferred: `guide.tour` (multi-marker), `guide.capabilities` UI.

### 6.2 Act visual verbs (computer-use / browser pack)

| Verb | Risk | Prefer order |
|---|---|---|
| `browser.result.open` | R2 | (1) Chrome/CDP or AX: Nth organic result URL → `site.open`  
|  |  | (2) Vision: POINT on first result + **confirm** → `InputSynth.click` at mapped coords |
| `ui.click` | R2 | Vision or AX target; always confirm unless dry-run |

Existing keystroke macros (`browser.tab.*`, etc.) stay in `computer-use` pack (off by default until trusted).

---

## 7. Latency budget (post-transcript)

| Path | Target | Notes |
|---|---|---|
| Verb / LLM plan only | &lt; 500 ms parse + handler | Already designed |
| Guide point | &lt; 3 s | Capture + vision + overlay; use fast vision model (Groq vision or similar) |
| Act first-result structured | &lt; 1 s | No vision |
| Act first-result vision+click | &lt; 4 s + confirm wait | User must approve click |

---

## 8. Modules (proposed)

```
src/vaani/vision/
  capture.py          # orchestrate PlatformBundle.screen
  shrink.py           # max-edge 1280, quality
  point_parse.py      # [POINT:…] / [CAPTION:…] → OverlayOp
  coords.py           # screenshot → global → overlay local
  guide_brain.py      # vision chat → tags + spoken line

src/vaani/surface/
  overlay.py          # IPC client to overlay helper
  # platform/<os>/overlay_app.*  native click-through windows

src/vaani/verbs/packs/
  guide.py            # guide.point / offer
  # extend browser or computer_use with browser.result.open
```

Wire into assemble: if screen+overlay available, register guide patterns; plan catalog card includes them when enabled.

---

## 9. Privacy & permissions

- Screen Recording (macOS) / analogous OS permissions — prompt once at first guide/act visual use.
- Capture tied to the utterance that needs it; cancel mid-flight drops the frame.
- History: `verb`, `question`, `rung`, optional `overlay` labels/coords — **no image blobs**.
- Confirm UI for clicks shows: “Click ‘Ramayana - Wikipedia’ at (x,y)?” or the resolved URL if structured path won.

---

## 10. Build slices

| Slice | Delivers | Gate |
|---|---|---|
| **V0** | Coord math + POINT parser + fake overlay (unit) | Fixture screenshot → deterministic ops |
| **V1 Guide** | macOS capture + vision + real overlay; `guide.point` live | “Where’s X on this screen?” points correctly on Retina + 2 displays |
| **V2 First-result act** | `browser.result.open` structured path (AX/CDP/url) + confirm | After Google SERP, “open first result” opens the right link without vision when possible |
| **V3 Vision click fallback** | If structured fails → POINT + confirm + `InputSynth.click` | Same utterance still works on sites without CDP |
| **V4** | Windows overlay/capture parity; Linux X11; Wayland honest UNSUPPORTED | caps matrix green |

---

## 11. Acceptance examples

| Utterance | Expected |
|---|---|
| Open Chrome and search Ramayana | Plan → site.search (no screen) — already works |
| Where’s the address bar? | `guide.point` → overlay on omnibox |
| Open the first result | `browser.result.open` {index:1} → confirm → navigate/click |
| Open first side for me (ASR) | Plan LLM maps to `browser.result.open`, **not** `site.search("first side")` |
| How do I free port 3000? | `guide.offer` / procs find + confirm offer (may skip screen) |

---

## 12. Risks

| Risk | Mitigation |
|---|---|
| Vision slow/expensive | Don’t capture unless needed; shrink frames; fast model |
| Wrong click | Confirm + URL prefer; show label in pill |
| Coord bugs on scaled displays | Fixture matrix in V0 before shipping overlay |
| Scope creep into full Clicky clone | Explicit non-goals; Vaani brand/triggers unchanged |

---

## 13. Open items (defaults)

1. Vision provider for guide: **Groq vision-capable model** if available, else OpenAI/Anthropic behind settings — same key patterns as groq client.  
2. Overlay process: extend indicator vs new `vaani.overlay` helper — decide in V1 plan (prefer one helper binary/module).  
3. CDP optional pack vs AX-only for V2 — prefer AX/URL first on macOS Safari/Chrome accessibility; CDP later.

---

## 14. Success metrics

- Guide: p50 &lt; 3 s from release to pointer visible; zero frames on disk.
- First-result: ≥70% structured (no vision) in dogfood on Google SERP; remainder vision+confirm.
- Zero regressions: existing verb/plan corpus still green with vision mocked to raise (same invariant as brain mock for rung 1/2).
