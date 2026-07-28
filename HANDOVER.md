# Vaani — Handover (2026-07-28)

Living handoff for the next session. Covers **what is running**, **what we built**, **product direction / ideas from chat**, and **what not to waste time on**.

---

## 1. Repo & branch (READ FIRST)

| Item | Value |
| --- | --- |
| Canonical path | `/home/gaurav/Gaurav Projects/Vaani/03-REPOSITORIES/control/Vaani-main` |
| Symlink | `/home/gaurav/Vaani/Vaani-main` → same tree |
| Git | **Yes** — this directory is its own git repo |
| Active work branch | `feat/assistant-qa-pill-critical` @ `058212e` (pushed to `origin`) |
| Safe lock (do not regress casually) | `save/stable` / `save/2026-07-27-locked` @ `1009ce6` |
| Remote | `https://github.com/0-Gaurav-0/Vaani.git` |
| Autostart | `~/.local/bin/vaani-start` → hardcoded to this `Vaani-main` path |

```bash
cd "/home/gaurav/Gaurav Projects/Vaani/03-REPOSITORIES/control/Vaani-main"
git status -sb
git log -5 --oneline
```

**Restart (only when Idle — never mid-recording/processing):**
```bash
pkill -f '^\.venv/bin/python -m vaani$' || true
sleep 1
setsid -f env VAANI_PROJECT_DIR="$PWD" "$HOME/.local/bin/vaani-start"
tail -n 20 ~/.local/share/vaani/logs/vaani.log
```

Logs: `~/.local/share/vaani/logs/vaani.log`  
History DB: `~/.local/share/vaani/history.sqlite3`  
Day memory: `~/.local/share/vaani/memory/YYYY-MM-DD.md`  
Config sites: `~/.config/vaani/sites.json`  
`.env` has live `GROQ_API_KEY` (gitignored — do not commit).

---

## 2. How to use it today (Linux ThinkPad)

| Input | Mode |
| --- | --- |
| Hold **ThinkPad middle button**, release | Dictation (smart cleanup by default) |
| **Double-press + hold** middle button | Assistant |
| Esc / pill cancel | Cancel |
| Pill check while processing | Cancel wait |

Dictation: STT → optional cleanup → paste into focused app (terminals often get Ctrl+Shift+V via `paste_target.py`).

Assistant: STT → hybrid route → open / play / Q&A / skill / Codex / paste / clarify.

---

## 3. What we built (this lineage — branch `feat/assistant-qa-pill-critical`)

Commits of note: `b49e18a` (Q&A pill + NL router + critical UX), `058212e` (open apps/sites/browse expansion).

### 3.1 Dictation / reliability
- ThinkPad middle-button hold-to-talk (not Ctrl+Space as primary)
- `vaani-toggle` is **stop-only** (SIGUSR1) — avoids ghost starts
- Prompt-bleed guard (`guard_transcription`) — rejects Whisper echoing the prompt
- Cleanup: `needs_cleanup()` local heuristic; skip Groq when clean; tight `cleanup_max_tokens`; light local filler strip
- Delivery status drives feedback (success vs clipboard-only vs fail) — improved vs older “always success” masking
- Ctrl+Shift+V for terminal paste targets (`paste_target.py` + AT-SPI where needed)

### 3.2 Assistant — Q&A pill + memory
- Questions / recommend-style asks → Groq `answer()` 
- Answer in **expanded recording pill** (Q above A); no notify-send for Q&A
- Reverse morph when starting a new recording while answer visible
- Day-scoped memory: `memory.py` → `~/.local/share/vaani/memory/YYYY-MM-DD.md` loaded into `answer(..., context=...)`

Specs:
- `docs/superpowers/specs/2026-07-27-assistant-qa-pill-critical-fixes-design.md`
- `docs/superpowers/specs/2026-07-27-assistant-day-memory-design.md`

### 3.3 Assistant — hybrid NL router
- Fast path: deterministic `resolve_app` / `resolve_youtube` / `resolve_site` / skill match (**no LLM**)
- Else Groq `route()` → JSON `intent` ∈ `{play, open, qa, codex, skill, paste, clarify}`
- Clarify: numbered options in pill; **click or speak** (“1”, “YouTube”, …); ~45s timeout
- Polite prefixes: “can you play…”, Hinglish play/open verbs (`kholo`, `dikhao`, `jao`, …)

Files: `assistant_route.py`, `assistant_intent.py`, `groq.route()`, `controller._process_assistant`

Spec: `docs/superpowers/specs/2026-07-27-assistant-nl-router-design.md`

### 3.4 Open / play / browse (beyond YouTube)
- Play/watch → YouTube watch URL (or OTT search if Prime/Netflix/Hotstar/SonyLIV named)
- Open apps: expanded catalog (Cursor, Chrome, Slack, Spotify, Firefox, …) + `resolve_app_name`
- Open sites: Gmail, GitHub, Wikipedia, LinkedIn, Notion, Drive, … + `sites.json`
- `resolve_browse_query`: known site **or** domain (`github.com`, “stackoverflow dot com”) **or** Google search
- AI `open` intent uses app-name / browse helpers (not spell-only English)

Files: `apps.py`, `sites.py`, controller `_execute_route_decision`

### 3.5 Skills / Codex
- Skill index under `~/.agents/skills` / `~/.claude/skills` → `CodexRunner.run_skill` with declared MCPs only
- Unmatched coding asks → Codex ephemeral runner
- Still **partial** vs full confirm-gated agent product (see ideas)

### 3.6 UX polish
- Answer card horizontally centered; don’t persist answer geometry as recording pos
- Processing shows right check (cancel)
- Reduced notify spam / silent success-paste sounds (keep recording start sound)
- Bare `play <title>` → YouTube

### 3.7 Latency notes (honest)
- **Whisper after release** still dominates (~0.5–1.5s+). Cleanup skip helps post-Whisper only.
- WhisperFlow-style speed needs **streaming STT** (roadmap later) — not done.
- Groq transcription API is batch file upload, not true streaming.

---

## 4. Architecture (current + intended product spine)

### Current assistant pipeline
```text
STT (Groq Whisper)
  → clarify pending? match speech/click
  → fast: app | youtube/site | skill
  → else Groq route JSON
       → play / open / qa / codex / skill / paste / clarify
```

### Intended spine (from product discussion — not all built)
```text
Voice
  → Groq: route + instant Q&A + cheap context attach
  → Deterministic: open / play / (future: mute, etc.)   # never wake Codex
  → Codex CLI: real multi-step work (research, browse MCP, code)
       optimized: tight brief, ephemeral, low reasoning, time budget, pill progress
  → Context waterfall (planned):
       URL → CLI/API/MCP entity
       → clipboard text/image
       → screen region / vision
       → clarify
```

**Rule:** If it’s one known action → don’t wake Codex.  
If it needs browsing / multi-step work / code → Codex, as fast as we can make that path feel.

---

## 5. Key files

| Area | Path |
| --- | --- |
| Orchestration | `src/vaani/controller.py` |
| Groq STT / cleanup / answer / route | `src/vaani/groq.py` |
| NL route parse / clarify match | `src/vaani/assistant_route.py` |
| Heuristic fallback intents | `src/vaani/assistant_intent.py` |
| Apps / sites / play | `src/vaani/apps.py`, `sites.py` |
| Day memory | `src/vaani/memory.py` |
| Terminal paste | `src/vaani/paste_target.py` |
| Pill / answer / clarify UI | `platform/linux/indicator_app.py`, `feedback.py`, `indicator_protocol.py` |
| Skills / Codex | `skills.py`, `codex.py` |
| Linux runtime | `platform/linux/runtime.py` (+ wayland/) |

Tests: `tests/unit/test_{assistant_route,assistant_intent,sites,apps,groq,controller_skills,memory,paste_target,feedback,…}.py`  
Run: `.venv/bin/python -m pytest tests/unit -q`

---

## 6. Ideas backlog (this chat — track & prioritize)

Full tracker: `docs/superpowers/specs/2026-07-28-vaani-ideas-tracker.md`  
Operator-actions plan: `docs/superpowers/plans/2026-07-28-operator-actions-beyond-youtube-plan.md`  
**Phased design (2026-07-28):** `docs/superpowers/specs/2026-07-28-transcript-open-operator-phased-design.md`

| Phase | Status | What shipped |
| --- | --- | --- |
| 0 STT reliability + Latin Hinglish (double STT) | **done** (`b1c4906`) | silence gate, bleed/junk guard, en+hi pick |
| 1 folders + apps.json + XDG | **done** (`6133bf8`) | Downloads/…, user apps.json, `.desktop` open |
| 2 mute/volume | **done** (`fcbd2df`) | pactl allowlist fast path |
| 3 confirm-gated Codex | **done** (`5d7ee71`) | Confirm/Cancel pill before agent |
| 4 computer-use | **deferred** | brittle on Linux/Wayland — not started |

| ID | Idea | Status |
| --- | --- | --- |
| I-01 | Full hybrid agent (fast catalog + tool agent + Codex + computer-use later) | exploring |
| I-02 | Groq fast / Codex for execution-heavy tasks; optimize Codex wall time | exploring |
| I-03 | Q&A personas (dev / CEO / PM / designer / tech) | noted |
| I-04 | **Clipboard-aware Q&A** — copy error, ask “what’s this?” | **recommended next** |
| I-05 | On-demand screen vision (“can you see that error?”) | later — not every press |
| I-06 | Active URL → Basecamp/GitHub CLI·API·MCP fetch entity | strong — after clipboard |
| I-07 | Context waterfall: URL/MCP → clipboard → screenshot → clarify | unifying design |

### Product judgment (from critique in chat)
**Definitely do near-term**
1. Clipboard text → Q&A (magical, shippable, daily use)
2. Harden Groq→Codex handoff (pill progress, time budget, tight brief) — confirm gate shipped
3. Keep open/play/mute instant (never Codex)
4. One integration done well (Basecamp *or* GitHub) for URL→CLI context

**Do later**
- Screenshot / focused-window vision
- Lock / brightness / window focus
- Computer-use research spike

**Don’t / not yet**
- Screen capture on every assistant press (privacy + tokens)
- “Agent that clicks anything” as core (brittle on Linux)
- Building OAuth for every SaaS (reuse user CLIs/MCPs)
- Vision before structured context
- Shipping five half-broken integrations

**Hard / not solid at this state**
- Reliable focused **browser URL** on all Wayland setups without a helper
- Truly instant multi-site research (Codex can do it; set “fast for an agent” expectations)
- Trustworthy general computer-use

**Cool + useful demo bets**
1. Copy stack trace → “fix this?” → pill answer  
2. On a GitHub PR / Basecamp todo → “what’s left / help me with this” via CLI context  
Not: feature zoo or always-on screen watching.

**Privacy chrome (should ship with context features):** always show what was used — “Using clipboard” / “Using Basecamp todo #123” / “Using screenshot”.

---

## 7. ROADMAP alignment

Phase 2 operator brain still largely `todo` in `ROADMAP.md`: P2-01…P2-07 (router was partially superseded by assistant hybrid router; confirm, system toggles, commands.json, window focus, user apps.json remain).

Streaming STT = P4-03 later (needed to feel like WhisperFlow).

---

## 8. Ops gotchas

1. **Duplicate processes** break hotkeys — always `pgrep -af 'python -m vaani'` before debug.
2. **`vaani-start` no-ops if already running** — kill first to pick up code changes.
3. Never restart mid-recording/processing (`docs/OPERATIONS.md`).
4. Don’t soften `TRANSCRIPTION_PROMPT` / reintroduce HTTP locks without cause.
5. Work on `feat/assistant-qa-pill-critical` or branch from it; don’t casually rewrite `save/stable`.

---

## 9. Suggested next session order

1. **Restart Vaani** (Idle) to pick up Phase 0–3; smoke: muted mic, Hindi phrase, `open vs code`, `open downloads`, `mute`, Codex confirm  
2. Design + implement **clipboard text Q&A** (I-04) with visible “Using clipboard” affordance  
3. Spec **context waterfall** (I-07) including URL detection approach on X11  
4. Pick **one** integration (Basecamp vs GitHub) for URL→CLI  
5. Codex path speed UX (progress phases in pill, budgets)  
6. Only then: vision fallback / lock-brightness / computer-use spike  

Do **not** start computer-use or always-on screen capture until clipboard + one CLI integration feel inevitable.

---

## 10. Historical note (older handover content)

An earlier 2026-07-27 handover focused on X11 autorepeat investigation, Wayland backend scaffolding, and a then-incorrect “no git in Vaani-main” claim. That investigation context is superseded for day-to-day handoff:

- Git **does** exist; branch work is on GitHub.
- Primary input is ThinkPad middle-button, not Ctrl+Space.
- Delivery-status masking was addressed in the assistant/dictation critical-fixes work; re-verify if paste regressions return.
- Wayland package still exists under `platform/linux/wayland/` — treat as partial / needs real compositor validation.

If diagnosing hotkey flicker on X11 again, see git history and older notes in agent transcripts; don’t assume the peek-autorepeat story is the current top priority versus clipboard Q&A / agent context.

---

## 11. Changelog (this doc)

| Date | Update |
| --- | --- |
| 2026-07-28 | Phases 0–3 shipped: STT guard/double-STT, folders/XDG/apps.json, mute/volume, Codex confirm; computer-use deferred |
| 2026-07-28 | Rewrote handover: shipped assistant features, architecture, ideas I-01…I-07, do/don’t, next steps |
| 2026-07-27 | Prior handover (autorepeat / Wayland / no-git assumptions) |
