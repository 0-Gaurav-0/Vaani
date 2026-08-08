# Async agent handoff + desktop notifications

**Date:** 2026-08-05  
**Status:** Approved  
**Scope:** Linux assistant → Hermes handoff UX (pill lifetime, Esc, notifications)

## Goal

After voice assistant hands a task to Hermes, Vaani must not keep the processing pill up for the whole agent run. The user should be free to dictate or start another handoff immediately. Progress and completion use the Linux notification panel; cancel after handoff is done in Hermes (notification click opens the session).

## Behavior

| Phase | Pill | Esc | Notification |
|---|---|---|---|
| Recording / STT / routing | Visible | Abort Vaani work | — |
| Hermes subprocess accepted prompt | Dismiss; Idle | No-op | “Started” with task preview; click opens Hermes session |
| Agent running (background) | Gone | No-op (use Hermes) | — |
| Agent finished | Gone | No-op | “Done” / “Failed” with preview; click opens session |

## Handoff “accepted”

Hermes oneshot often creates the SQLite session row late (MCP discovery / agent build). Waiting on that row would keep the pill up for many seconds.

**Definition used:** handoff is accepted when `hermes -z` has been successfully `Popen`’d. Session id is discovered asynchronously (usage-file at end, and/or `state.db` poll by cwd + start time) for notification deep-links.

## Notifications

- App name: `Vaani`
- Started: title `Vaani → Hermes`, body = short task preview; action **Open**
- Done: title `Vaani agent · Done` (or Failed / Cancelled); body = preview + short result snippet; action **Open**
- Open: focus/launch Hermes Agent on that session when possible (`hermes://session/<id>` / `hermes-desktop`); else focus the app (session under **Vaani agent**)

Hermes pets are not driven by Vaani.

## Esc

- Only while Vaani is Recording/Processing **before** Idle release.
- After release: Esc must not cancel background Hermes (would steal Esc from other apps).

## Non-goals

- Desktop gateway RPC create/submit (follow-up)
- Changing open/play/volume fast paths
- Cross-platform notifications beyond Linux GI Notify / notify-send fallback
