# Assistant App and URL Launchers

## Goal

Extend Vaani assistant mode so common desktop applications and frequently used
web destinations open immediately from a voice command without waiting for
Codex.

## Behavior

- Commands must contain an action such as `open`, `launch`, or `start`.
- Desktop applications are resolved from a fixed allow-list and launched
  directly without a shell.
- `Open Claude` launches Claude Desktop. `Open Claude website` opens Claude in
  the browser.
- Web destinations use Brave unless the command explicitly names Chrome.
- Stripe opens the stable Stripe dashboard URL rather than a customer-specific
  history URL.
- Unknown commands continue to use the existing Codex assistant path.

## Initial App Catalog

- Claude Desktop
- Terminal
- Text Editor
- Visual Studio Code
- Files
- Calculator
- Settings
- Calendar
- System Monitor
- DBeaver
- MongoDB Compass
- LibreOffice, Writer, Calc, and Impress
- Cursor
- Antigravity
- Thunderbird
- Remmina
- Image Viewer, Document Viewer, and Archive Manager
- Tweaks

## Validation

- Unit tests cover aliases, action-word requirements, desktop/web
  disambiguation, executable fallback, and safe process arguments.
- Existing site tests cover Stripe and browser selection.
- The complete Vaani test suite must pass.
