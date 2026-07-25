# Vaani Operations Rules

## Safe restart rule

Never restart or kill the Vaani process while a recording or transcription is
active. Wait until the log reports `event=delivered`, `event=cancelled`, or
`event=failure` before applying code/configuration changes. This preserves the
recording and prevents silent loss of dictated text.

If a restart is unavoidable, notify the user first and explicitly cancel the
recording so the interruption is visible rather than presenting it as a
completed run.

## Private site shortcuts

Keep private URLs outside Git in `~/.config/vaani/sites.json`. Copy the schema
from `config/sites.example.json` and add aliases, a display name, and an HTTP or
HTTPS URL for each site. Missing or invalid entries are ignored safely.

Set `VAANI_ASSISTANT_CWD` to choose the directory used for Codex assistant
requests. The default is the user's home directory.
