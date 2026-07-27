# Task 10 — Wire assemble / controller / CLI + enable guide default

## Delivered

- `Controller` now owns lazy guide dependencies: screen capture, file-backed
  overlay, and a guide-brain getter. It displays returned guide overlays for
  eight seconds and clears them for new capture, cancellation, and successful
  non-guide results.
- `assemble()` wires the platform screen adapter, the indicator-adjacent
  `FileOverlay`, and `make_guide_brain(groq, controller.key_provider)`.
- CLI registry construction supplies best-effort screen and overlay adapters;
  a missing screen capture produces a normal unsupported result rather than a
  traceback. The guide pack is enabled by default.
- `Settings.overlay_path` now names the IPC `overlay_ops` file used by the
  indicator and `FileOverlay`.

## Tests

- `.venv/bin/python -m pytest tests/unit/vision tests/unit/surface/test_overlay_ipc.py tests/unit/verbs/test_guide_pack.py tests/unit/test_controller.py tests/unit/test_assemble.py tests/unit/test_config.py -q`
  — 53 passed.
- `.venv/bin/python -m pytest tests/unit -q` — 1011 passed, 1 skipped.
- `.venv/bin/python -m vaani caps --json` — guide pack is enabled and guide
  verbs are listed.
- `.venv/bin/python -m vaani do guide.point --slot target="address bar" --json --dry-run`
  — returned a `dry_run` JSON result without a traceback.

## Notes

- The guide pack now returns overlays to the controller for rendering, so
  standalone handlers stay side-effect-free and controller lifecycle rules
  apply consistently.
