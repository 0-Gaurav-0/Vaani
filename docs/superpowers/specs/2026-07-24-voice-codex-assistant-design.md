# Voice Codex Assistant Mode

## Goal

Extend Vaani with a separate voice-command mode that sends spoken tasks to Codex CLI, while preserving the existing Ctrl+Space dictation workflow unchanged.

## User experience

- `Ctrl+Space` starts/stops normal dictation and pastes the result.
- `Ctrl+Alt+Space` starts/stops assistant recording.
- `Esc` cancels either active recording mode.
- Assistant requests run in the user's home directory by default. Set
  `VAANI_ASSISTANT_CWD` to select another workspace.
- Recording, processing, and completion/failure are visible through the existing indicator and local logs.
- Codex output is shown in a result window and saved to local history.

## Architecture

1. Add an assistant hotkey route to the existing signal/custom-shortcut mechanism.
2. Reuse the existing recorder, Groq transcription, cancellation, indicator, and history components.
3. Add an assistant service that invokes Codex CLI with the final transcript as the task and the configured workspace as its working directory.
4. Keep assistant execution separate from `ClipboardDelivery`; assistant results are displayed, not pasted into the focused application.
5. Capture stdout, stderr, exit status, and timeout state. Never log API keys or full sensitive environment values.

## Safety

- Codex CLI remains responsible for its own command/edit confirmations.
- Vaani must not silently approve shell commands or destructive operations.
- Assistant mode must reject concurrent requests while one is processing.
- `Esc` cancels recording and attempts to terminate an active Codex process.
- A configurable timeout prevents an orphaned request.

## History and errors

Store the spoken request, normalized transcript, Codex response, status, workspace, and timestamps in the existing local history database (or an additive assistant-history table). Show actionable failure messages for missing Codex CLI, missing API/auth configuration, timeout, and non-zero exit.

## Verification

- Existing dictation tests remain green.
- Unit-test assistant command construction, workspace selection, cancellation, timeout, and redaction.
- Integration-test a fake Codex executable and verify output-window delivery and history persistence.
- Manually verify both shortcuts in a text editor and terminal without changing normal paste behavior.
