# OpenClicky architecture lessons for Vaani

**Status:** reference notes (clone can be deleted after reading)  
**Date:** 2026-07-27  
**Source clone (temporary):** `/Users/shubham/Desktop/Projects/_tmp/openclicky`  
**Upstream:** https://github.com/jasonkneen/openclicky  
**Vaani policy:** Steal **visuals + routing discipline**. Keep Vaani’s verb compiler / confirm / packs. Do **not** clone the Swift menu-bar buddy, TTS personality, or full Agent/CUA OS.

---

## 1. What OpenClicky is (three lanes)

| Lane | What it does | Vaani analogue |
|---|---|---|
| **Voice guide** | Hotkey → screenshot → model → `[POINT:]` / RECT overlay; **does not** warp system cursor | `guide.point` + overlay IPC |
| **Direct act** | Open app/URL, keys, clicks via native routes **before** agent | `app.open`, `site.search`, `browser.result.open` |
| **Agent / CU** | Codex + skills + optional `cua-driver` MCP; **last resort** for GUI operate loops | `agent.task` + `computer-use` pack (thin today) |

OpenClicky’s own CUA skill (`AppResources/.../cua-driver/SKILL.md`) states: snapshot → prefer AX `element_index` → re-snapshot; never shell `open`/`cliclick` in that lane; structured MCP beats pixel clicks.

---

## 2. Capture contract (steal)

**Source:** `cursor-buddy/CompanionScreenCaptureUtility.swift` → `CompanionScreenCapture`

| Field / rule | OpenClicky | Vaani today |
|---|---|---|
| Long edge | 1280 JPEG ~0.8 | `vision/shrink.py` |
| Labels | `screen N of M — cursor is on this screen (primary focus)` | `vision/capture.py` labels (thinner) |
| Geometry | Keep **AppKit `displayFrame`** (origin + points) **separate** from screenshot pixel size | `ScreenFrame` + `DisplayGeom` |
| Multi-monitor | Cursor screen first; agent mode often **cursor screen only** | Primary-focused / stubs |
| Exclude self | Strip own overlay windows from SCKit filter | Partial / best-effort |
| Prewarm | Cache `SCShareableContent` ~3s on key-down | Not yet |
| API | ScreenCaptureKit first | Pillow `ImageGrab` (permission-fragile) |

**Coord map (steal math, already mirrored):** screenshot top-left pixels → scale to display points → **Y-flip** → add `displayFrame.origin` → optional calibration. Bridge `/cursor` uses **global AppKit points**.

**Source:** `CompanionManager+AIResponsePipeline.swift` `globalPoint(fromScreenshotPoint:in:)`.

---

## 3. Tag protocol (steal subset)

**Source:** `CompanionManager+PointTagParsing.swift`

| Tag | Implemented in OpenClicky? | Vaani |
|---|---|---|
| `[POINT:x,y:label]` / `:screenN` | Yes | Yes (`point_parse.py`) |
| `[POINT:none]` | Yes | Optional |
| `[RECT:…]` / `[SCRIBBLE:…]` | Yes | Later |
| `[TYPE:…]` (README) | **Not in Swift parser** | Skip; use CAPTION / POINT label |
| `[CAPTION:…]` | Via bridge / labels | Vaani has CAPTION |

Guide rule: **proxy triangle flies to target and returns**; system pointer stays put. Real warp/`click` is a **separate act path**.

---

## 4. Routing ladder (steal policy, not the Swift monolith)

Approximate order in `CompanionManager.routeFinalVoiceTranscriptActionIfNeeded`:

1. Cancel / clear overlays / status  
2. Affirmative confirm of pending agent offer (“yes”, “okay”…)  
3. Same-session agent follow-up (`sessions_send`-like)  
4. Explicit / hybrid agent start (`sessions_spawn`-like)  
5. **Direct computer-use / app-URL routes**  
6. Quick local answers  
7. Else → **screen-aware voice** (screenshot + POINT)

Product README policy: answer → web search → integrations → agent → **computer-use last**.

**Vaani mapping:** grammar → LLM plan → typed verbs first; vision only for `guide.*` / act fallback; never invent storefront URLs; never CU-first.

---

## 5. Confirm & sessions (steal lightly)

| OpenClicky | Mechanism | Vaani |
|---|---|---|
| Offer → speak yes | `pendingAgentOfferInstruction` + `isAffirmativeConfirmation` | ConfirmEngine + Enter/pill; voice “confirm” still weak |
| Spawn new agent | new Codex thread | `agent.task` / wake phrase (half-built) |
| Continue session | same thread `turn/start` | AgentRunner ~10 min continuity only |

Hands-free AirPods Nodex: **skip** for now.

---

## 6. Control bridge (defer)

`OpenClickyExternalControlBridge.swift` on `127.0.0.1:32123`: `/cursor`, `/cursors`, `/caption`, `/screenshot`, `/click`, `/speak`, `/clear`, `/events`, MCP. Read-only until confirmed for write-ish tools.

Vaani has file IPC overlay + remote stubs. Full bridge parity is **P5**, not dogfood.

---

## 7. Steal vs skip (locked)

| Idea | Decision |
|---|---|
| Labeled capture + 1280 JPEG + displayFrame metadata | Steal / harden |
| Screenshot→global Y-flip | Steal (already in `coords.py`) |
| Proxy overlay point, no cursor warp for guide | Steal |
| Direct open/search before vision/CU | Steal |
| CU last; AX before pixels | Steal as policy |
| Unknown app → search (not fake URL catalog) | Steal |
| Snapshot→act→re-snapshot loop for CU | Steal **one-shot verify** for first-result only |
| Full cua-driver MCP / Codex Agent Mode | Skip |
| Continuous tutor / speculative pre-fire | Defer |
| Swift OverlayWindow / notch / TTS buddy | Skip |
| `:32123` bridge | Defer |
| RECT/SCRIBBLE tours | Defer |

---

## 8. Why Vaani dogfood fails (mapped to OpenClicky)

| User step | OpenClicky-like behavior | Vaani bug |
|---|---|---|
| Search Blinkit | Direct URL/search route | Works (`site.search`) |
| Open first result | Structured href **or** vision point + **click** (act) | Vision fallback **unwired** in `register_browser_results_pack` |
| Open Blinkit | Launch app if installed; else search/agent — **no invent URL** | `app.open` → FAIL when not desktop app |

Root cause is not “missing OpenClicky clone” — it’s **incomplete integrate** of already-written Vaani vision code + missing search fallback.

---

## 9. Temp clone cleanup

When notes + plan are committed and no longer needed for line-level reading:

```bash
rm -rf /Users/shubham/Desktop/Projects/_tmp/openclicky
```

Do **not** add OpenClicky into the Vaani git tree.
