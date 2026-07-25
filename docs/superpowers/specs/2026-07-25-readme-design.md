# Vaani README Design

**Date:** 2026-07-25
**Audience:** The primary user maintaining, reinstalling, operating, and
developing Vaani on Ubuntu

## Goal

Create one comprehensive `README.md` that is sufficient to understand Vaani,
install it on a fresh supported machine, configure credentials safely, operate
every mode, diagnose common failures, run the test suite, and recover the
installation without relying on chat history.

## Scope

The README will cover:

- product purpose, feature summary, and current Linux/X11 support boundary;
- verified system and Python prerequisites;
- installation and first-run checks;
- Groq credential storage through the system keyring;
- manual launch and GNOME autostart;
- smart, literal, answer, and Codex assistant modes;
- all implemented shortcuts and cancellation controls;
- private site aliases and assistant workspace configuration;
- data locations, transcript history, logs, temporary audio, and privacy;
- architecture, processing flow, and a concise source-module map;
- safe restart rules;
- symptom-oriented troubleshooting;
- development, testing, and secret-scanning commands;
- backup and recovery steps;
- current limitations and security notes.

The README will not claim Windows, macOS, Wayland, packaging, or workflow
learning support that the current repository does not implement.

## Accuracy Rules

Every shortcut, command, path, package, environment variable, and behavior must
be checked against the current source or executed locally where safe.

Commands will:

- use repository-relative paths;
- contain placeholders rather than real credentials;
- avoid machine-specific usernames and private URLs;
- distinguish required dependencies from optional Codex assistant tooling;
- avoid restarting the currently running stable Vaani service during
  validation.

Unknown or environment-dependent behavior will be labeled instead of guessed.

## Structure

The README will begin with a concise overview and quick-start path, followed by
progressively deeper operating and development material. Tables will be used
only where they make shortcuts, modes, paths, or troubleshooting mappings
easier to scan.

Code examples will be copyable shell commands. Security-sensitive examples will
use environment variables or keyring prompts and will never place a real key in
Git.

## Verification

Before publication:

1. Validate shell snippets syntactically and execute non-destructive repository
   checks.
2. Cross-check shortcuts and behavior against `hotkeys.py`, `controller.py`,
   `groq.py`, `sites.py`, and launcher scripts.
3. Run the complete test suite.
4. Run secret-pattern and private-information scans across the final tracked
   tree.
5. Confirm the GitHub repository remains private and the original stable Vaani
   process remains unchanged.

## Success Criteria

- A fresh reader can install and start Vaani without prior chat context.
- Every implemented operating mode and shortcut is documented accurately.
- Troubleshooting starts from observable symptoms and gives safe checks.
- No credential, private URL, personal machine path, or internal operational
  information appears in the README or commit.
- Tests and publication-safety scans pass before the update is pushed.
