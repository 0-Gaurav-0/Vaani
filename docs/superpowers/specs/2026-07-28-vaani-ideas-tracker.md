# Vaani context & agent ideas tracker

Date started: 2026-07-28  
Status: ideas backlog (not approved designs unless noted)  
Branch context: `feat/assistant-qa-pill-critical`

Keep this file updated as product ideas land in chat. Prefer **structured context** over pixels when possible.

---

## Idea index

| ID | Idea | Status | Notes |
| --- | --- | --- | --- |
| I-01 | Hybrid agent program (fast catalog + tool agent + Codex + computer-use later) | exploring | Build order: D → B → C → A |
| I-02 | Groq for fast path; Codex CLI for slow/execution tasks | exploring | Optimize Codex for min wall time |
| I-03 | Q&A use cases by persona (dev/CEO/PM/designer/tech) | noted | Pill Q&A + day memory |
| I-04 | Clipboard-aware Q&A (copy error → ask about “this”) | exploring | Text first; screenshots next |
| I-05 | On-demand screen capture / vision (“can you see that error?”) | exploring | Prefer focused window; not every press |
| I-06 | **Active-URL / app context via CLI·API·MCP** (e.g. Basecamp) | exploring | Token-efficient; screenshot fallback |
| I-07 | Context waterfall: URL/MCP → clipboard → screenshot → ask user | exploring | Unifies I-04, I-05, I-06 |

---

## I-06 / I-07 — Active context waterfall (latest)

### User intent
When asking about “this” while in a tool (e.g. Basecamp todo):

1. Detect **current browser URL** (or focused app).
2. If URL matches a known integration (Basecamp, GitHub, Linear, …), call that tool’s **CLI / API / MCP** to fetch the **structured object** (todo, card, PR, issue).
3. Answer / act with that rich, compact context (low tokens, no scrolling).
4. If integration fails or URL unknown → fall back to **clipboard**, then **screenshot/vision**, then clarify.

### Why better than screenshot-first
- Fewer tokens than images
- Exact fields (title, description, assignees, comments) vs OCR guessing
- Stable for agent/Codex prompts
- Screenshot remains a **fallback**, not the default

### Sketch

```text
Assistant ask about "this" / "that error" / current work
        │
        ▼
┌───────────────────┐
│ Resolve context   │
│ 1. Focused URL    │──match──► Integration (Basecamp CLI/MCP, gh, …)
│ 2. Clipboard text │           fetch entity → inject into Groq/Codex
│ 3. Clipboard image│
│ 4. Screen region  │──vision──► caption/OCR → inject
│ 5. Clarify        │
└───────────────────┘
```

### Open design questions
- URL detection: browser extension vs AT-SPI vs `xdotool`/portal (Linux X11 vs Wayland)
- Integration registry: `~/.config/vaani/integrations.json`?
- Auth: reuse existing CLIs (user already logged in) vs Vaani-owned OAuth
- When to attach context: only deictic asks (“this”, “that error”) vs always

---

## Related shipped / planned (for orientation)

Already on assistant branch (approx): NL router, open apps/sites, play media, Q&A pill, day memory, Codex/skills, clarify options.

Earlier plan doc (catalog-first OS actions — may be superseded in part by I-01 agent direction):

- `docs/superpowers/plans/2026-07-28-operator-actions-beyond-youtube-plan.md`

Architecture sketch (Groq fast / Codex execution): discussed in chat 2026-07-28; not yet merged into a dedicated agent design spec.

---

## Changelog

| Date | Update |
| --- | --- |
| 2026-07-28 | Created tracker; added I-01…I-07 including Basecamp URL→CLI waterfall |
| 2026-07-28 | Product critique folded into `HANDOVER.md` §6 (do / don’t / not yet / demos) |
