# Day-scoped assistant Q&A memory

**Date:** 2026-07-27  
**Status:** Approved

## Goal

Follow-up assistant questions reuse today’s prior Q&A (e.g. “detailed instructions” after “how do I make tea”).

## Storage

- Directory: `~/.local/share/vaani/memory/`
- File per day: `YYYY-MM-DD.md`
- Append-only blocks:

```markdown
## HH:MM
**Q:** …
**A:** …
```

## Runtime

- On assistant Q&A: load today’s file (last ~8 turns / ~3k chars) into Groq context
- After answer: append the new turn
- No cross-day recall; dictation/actions unchanged

## Also in this pass

- No notify-send for browser/app open results
- No success/paste completion “ting” sound; keep recording start sound
