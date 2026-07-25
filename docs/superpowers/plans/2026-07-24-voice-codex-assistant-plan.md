# Voice Codex Assistant Implementation Plan

## Task 1: Codex runner and result delivery

Add a tested service that invokes the Codex CLI in the configured assistant
workspace, captures stdout/stderr/exit status, enforces a timeout, supports
cancellation, and redacts secrets. Add a small result-window adapter with a
headless test seam.

## Task 2: Assistant controller and history

Add assistant-mode orchestration that reuses recording/transcription/cancellation, prevents concurrent requests, invokes the runner, displays the result, and persists request/response/status locally without changing dictation delivery.

## Task 3: Shortcut wiring and end-to-end verification

Wire `Ctrl+Alt+Space` through the existing GNOME shortcut/signal path, retain `Ctrl+Space` and `Esc`, add launcher scripts, update documentation, and test both modes plus cancellation.
