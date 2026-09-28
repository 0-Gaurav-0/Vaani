# Vaani operator actions — beyond YouTube (structured plan)

Date: 2026-07-28  
Branch context: `feat/assistant-qa-pill-critical` @ `058212e`  
Status: **planning only** (no implementation in this doc)  
Sources: ROADMAP Phase 2, council (Claude + Codex), current hybrid router

## 1. Problem

Vaani can already:

- Dictate / cleanup / paste
- Play media (YouTube / OTT)
- Open known apps + sites / domains / web search
- Q&A + day memory
- Codex / skills for coding

The gap the user cares about: **do more than open YouTube** — open *any* useful app, and **perform certain tasks** (system control, recipes, bounded workflows) without becoming an unconstrained desktop robot.

Today “task” mostly means *launch a process or open a URL*. Everything else falls through to Codex (slow, powerful, no confirm) or paste.

## 2. Council agreement (Claude ∩ Codex)

| Topic | Agreement |
| --- | --- |
| Core model | LLM is a **parser**, never an executor |
| Primary architecture | **Catalogued / registry actions** with validated args |
| Escape hatch | Skills / Codex / MCP — **confirm-gated**, not primary for “mute” |
| Reject as core | Generic desktop automation (xdotool click loops, AT-SPI computer-use, screenshot→VLM) |
| Safety | Separate **risk tier** from **confidence**; silence ≠ consent |
| Fit | Extend hybrid router + clarify pill; don’t rewrite into an agent OS |

### Nuance (disagreement / emphasis)

| Claude | Codex |
| --- | --- |
| P0 = `apps.json` + system toggles + playerctl + window focus | P0 = **XDG `.desktop` index** so “any installed app” is true |
| P1 = slotted deep links + `commands.json` + timers + tiny compounds | P1 = only **3 high-frequency recipes** first |
| Strong on registry size cap + router prompt budget + regression corpus | Strong on launching by desktop-id, never speech-derived `Exec` |

**Synthesis:** do both P0 tracks — user `apps.json` *and* XDG desktop discovery (aliases override XDG). Keep P1 small (≤3 recipes) while laying `commands.json` + confirm policy seams.

## 3. Approaches considered

### A. Catalogued actions + slot-filling router (recommended)

Typed `ActionSpec` / `TaskSpec` registry → router returns `{action_id, args}` → local validators → fixed executor.

- Pros: fast, testable, fail-closed, matches ROADMAP P2-01…P2-05
- Cons: coverage only what we enumerate

### B. Skills / MCP / Codex as primary

- Pros: high coverage
- Cons: 30–120s latency, non-deterministic, huge blast radius — wrong for mute/open

### C. Desktop computer-use

- Pros: reaches apps with no CLI
- Cons: brittle, focus-sensitive, Wayland-hostile, silently wrong — **non-goal**

**Recommendation: A, with B as gated escape hatch. Reject C as core.**

## 4. Phased capability plan

### Phase 0 — Open anything + machine controls (ROADMAP P2-02, P2-03, P2-07)

| Capability | Example utterances | Notes |
| --- | --- | --- |
| User app aliases | “Zed kholo”, “open Signal” | `~/.config/vaani/apps.json` |
| XDG installed apps | “Open Android Studio” | Index `.desktop`; aliases win |
| System toggles | “mute”, “volume 30”, “brightness down”, “lock screen”, “screenshot” | Allowlisted argv only (`pactl`, `brightnessctl`, `loginctl`, …) |
| Media transport | “pause”, “next track” | `playerctl` |
| Window focus | “switch to Chrome”, “Cursor pe jao” | `wmctrl` / safe activate — no synthetic clicks |

**Demo bar:** “open Cursor”, “mute”, “open \<app not in hardcoded list\>”.

### Phase 1 — Parameterized recipes (ROADMAP P2-04, P2-05, P2-10 start)

| Capability | Example utterances | Risk |
| --- | --- | --- |
| Site deep-link slots | “GitHub pe vaani repo dhoondo” | T0 if http(s) template only |
| User `commands.json` | “start my morning setup” | T1 confirm |
| 3 named recipes (product pick) | TBD by user | per-recipe tier |
| Timers | “remind me in 20 minutes…” | T0 create / T1 cancel-all |
| Tiny compounds ≤3 steps | “mute and lock” | T1 if any step is T1 |

**Invariant:** LLM never supplies argv. Recipes validate at load (`shell=False`, no metacharacters).

### Phase 2 — Gated agent (ROADMAP P2-05, P2-06)

| Capability | Example | Risk |
| --- | --- | --- |
| Confirm → Codex / skill | “fix the flaky test” | T1 always |
| MCP only if skill declares | existing skill index | default off for user MCP |

**Non-goals:** free-form shell from speech; send email/post/pay; `sudo`; file delete; computer-use loops.

## 5. Architecture sketch

```text
Speech → Whisper → hybrid router
                 ├─ fast path: app / site / play / system regex
                 ├─ Groq route → {intent, action_id, args, confidence, options}
                 └─ validate against registry + policy(tier)
                        ├─ T0 → execute
                        ├─ T1 → clarify/confirm pill (Run / Cancel)
                        └─ T2 / unknown → reject + notify
```

New modules (proposed):

- `actions.py` / `tasks.py` — registry + schemas
- `system_actions.py` — OS toggles executors
- `commands.py` — `commands.json` loader
- `policy.py` — T0/T1/T2 + `requires_confirm`
- Extend: `assistant_route.py`, `groq.route` prompt (generated action list), `controller` dispatch chokepoint
- Reuse: clarify pill as confirm UI (tighten yes/no matcher for T1)

## 6. Safety model

| Tier | Policy | Examples |
| --- | --- | --- |
| T0 | Auto if confidence OK | open app/site, play, mute/volume, screenshot, lock, focus, search |
| T1 | Always confirm in pill | `commands.json`, compounds, mailto compose, Codex/skills, overwrite files |
| T2 | Never from voice | free shell, sudo, delete, send/publish, credentials, non-http(s) schemes |

Hard rules:

1. No LLM string in argv positions  
2. Confirm timeout / dismiss = abort  
3. Fail closed on unknown action id  
4. Audit log: action_id, tier, confirm path  

## 7. Verifiable success criteria

1. Open an installed app **absent** from the static catalog  
2. “mute” / “open Chrome” never call `groq.route` (fast path)  
3. Adversarial route payloads never spawn subprocesses  
4. T1 with confidence 0.99 still does nothing until confirm  
5. Frozen regression corpus for existing play/open/qa intents stays 100%  
6. Registry size / router prompt token budget capped by tests  

## 8. Biggest over-scope risk

Turning “perform tasks” into an LLM desktop robot: slow, focus-wrong, hard to test, silently destructive after one STT miss. Mitigate with registry caps, prompt budget, and the frozen regression corpus.

## 9. Suggested implementation order (after design approval)

1. Policy chokepoint + confirm pill (P2-05 seam) — even before many actions  
2. `apps.json` + XDG app index (P2-02)  
3. System toggles allowlist (P2-03)  
4. Window focus (P2-07)  
5. Pick 3 P1 recipes + `commands.json` (P2-04)  
6. Gate Codex/skills behind confirm (complete P2-05/P2-06)

## 10. Open product question

Which class of “certain tasks” should define Phase 1 success for *you*?

- **A)** Machine control (mute / volume / lock / screenshot / focus)  
- **B)** Personal recipes (`commands.json` / morning setup / timers)  
- **C)** Work deep-links (Gmail/GitHub/Basecamp search-in-site)  
- **D)** Coding agent (confirm → Codex/skills) — already partial  

Default recommendation if unspecified: **A then B**, matching ROADMAP demo bar.
