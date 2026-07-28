# Fast Isolated Codex Assistant Mode

## Decision
Vaani's assistant-mode Codex invocation will prioritize immediate execution over interactive safety prompts, at the user's explicit request.

## Behavior
- Ignore the user's Codex config, including configured MCP servers.
- Ignore user/project execution-policy rules.
- Skip the Git repository trust check.
- Run Codex without approval prompts or sandbox restrictions.
- Add a concise high-priority instruction to voice-submitted tasks.
- Preserve ephemeral sessions and low reasoning effort.

## Safety boundary
This applies only to Codex tasks explicitly submitted through Vaani assistant mode. It does not change the user's global Codex configuration or regular Codex sessions.

## Verification
Unit tests assert every required CLI flag and the priority instruction. Vaani is restarted and its startup process is checked afterward.
