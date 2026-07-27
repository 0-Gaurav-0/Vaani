# Assistant natural-language router (hybrid)

Date: 2026-07-27  
Status: approved for implementation (user: do it; Approach 1)

## Goal

Assistant commands must work as natural speech (English / Hindi / Hinglish),
not as fixed spells. Example: “kya tum brand new day ka trailer chala sakte ho”
should play the trailer without requiring exact English wording.

## Decisions

- Scope: all assistant capabilities (play/open/qa/codex/skill/paste).
- Path: **hybrid** — deterministic resolvers first; Groq JSON router when unmet.
- Ambiguity: pill shows 2–5 options; user picks by **click or speech**; speaking
  something else abandons clarify and treats as a new request.
- Router decides; existing launchers execute.

## Flow

1. Transcribe (unchanged).
2. If a clarify session is pending, try match spoken choice (`1`, label, target).
3. Fast path: `resolve_app` / `resolve_youtube` / `resolve_site` / skill match.
4. Else Groq `route()` → structured decision.
5. Execute, or show clarify options and wait.

## Router schema

```json
{
  "intent": "play|open|qa|codex|skill|paste|clarify",
  "query": "string",
  "target": "youtube|prime|netflix|hotstar|sonyliv|app|site|",
  "confidence": 0.0,
  "options": [
    {"label": "Play on YouTube", "intent": "play", "query": "...", "target": "youtube"}
  ]
}
```

- Act when `intent != clarify` and `confidence >= 0.65` (and no forced options).
- Otherwise show `options` (2–5); if model omitted options, synthesize from
  plausible targets for media-like utterances, else paste.

## Clarify UX

- Reuse answer-phase pill: question = user utterance; body = numbered options.
- Click an option row → `option_N` control command → execute.
- Speak “1” / “YouTube” / option label → execute.
- New unrelated utterance clears pending clarify and re-routes.
- Auto-dismiss longer than Q&A (≈45s); dismiss abandons without paste.

## Non-goals

- Free-form multi-tool agent loops.
- Streaming STT.
- Changing dictation (non-assistant) path.
