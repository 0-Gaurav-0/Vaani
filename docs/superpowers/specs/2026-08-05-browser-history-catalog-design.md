# Design: Browser history catalog + source-aware open

Date: 2026-08-05  
Status: Approved for implementation

## Goals

1. Background sync of Chrome + Brave history into a local Vaani catalog.
2. Voice “open …” matches catalog and launches in the **source browser** (chrome vs brave).
3. **Recent** tier retained **30 days**; **main** tier for frequent / important targets.
4. Idle cost ≈ 0; sync is a short cron, not a daemon.
5. Local-only, hardened URL handling — no agent in the open hot path.

Hermes remains the coding/agent CLI (separate). History sync must never call Hermes.

## Non-goals

- Always-on history watcher
- Cloud upload of history
- Perfect NLP synonymy (local token/alias match only)
- Spawning Chromium/Selenium for sync

## Architecture

```
systemd timer (user) ──► vaani-history-sync
                              │
                              ├─ copy History DB (Brave, Chrome)
                              ├─ SQL: visits in last 30d
                              ├─ normalize URL, classify tier
                              └─ write ~/.local/share/vaani/history_catalog.sqlite (0600)

assistant open ──► sites.json → catalog main → PUBLIC_SITES → catalog recent
                              └─ SiteTarget(url, browser=chrome|brave)
```

### Sync efficiency

- Language: Python + stdlib `sqlite3` / `shutil` only (no new heavy deps).
- Per browser: copy History to temp (Chrome lock-safe), one parameterized query, drop temp.
- Budget: &lt;5s wall, &lt;100MB RAM, then exit.
- Default schedule: every 6 hours + on login optional.

### Tiers

| Tier | Rule | Retention |
|------|------|-----------|
| `main` | ≥5 visits in 30d window, or pinned in `sites.json` | Keep until 90d without visits |
| `recent` | Other visits in window | Hard delete when `last_visit` older than 30d |

Deep paths kept when path is “interesting” (e.g. `/dashboard/`, `/projects/`, non-root path with enough visits); otherwise host homepage preferred for main.

### Open matching

1. Explicit “brave” / “chrome” in utterance wins.
2. Else catalog `browser` field.
3. Else Vaani default (Brave).

Match: phrase against aliases/title/host (longest alias wins), https/http only.

### Security

- Catalog mode `0600`, dir `0700`.
- Schemes: `http`/`https` only.
- Exclude substrings/hosts from config (defaults: `chrome-extension:`, `chrome://`, banking-ish patterns configurable).
- Strip tracking params; never execute title/URL as code or shell.
- Sync runs as user; no new ports; no root.
- SQL always parameterized.

## Success criteria

1. After sync, catalog has Brave+Chrome rows with `browser` set.
2. “Open &lt;frequent site&gt;” opens in the browser that owns that history row.
3. Entries older than 30d leave `recent`; sync completes quickly on ~15MB Brave History.
4. Unit tests cover normalize, tiering, scheme reject, match+browser.
