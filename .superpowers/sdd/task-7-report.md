# Task 7 — Guide brain (vision chat) V1

## Delivered

- Added guide-point orchestration that sends screenshots to an injected vision
  client and converts its POINT reply into `OverlayOp` values.
- Added capture orchestration with model-facing labels such as
  `screen 1 of N — display M (WxH)`.
- Added Groq vision chat support using request-local base64 `image_url` data.
  The guide prompt defines top-left screenshot pixels, one terminal POINT tag,
  and a spoken line capped at 80 characters.
- Added a keyed `make_guide_brain()` adapter suitable for application wiring.

## Tests

- `tests/unit/vision/test_guide_brain.py` tests fake-client parsing, labeling,
  and key binding without network access.
- `tests/unit/test_groq.py` verifies the Groq payload contains a labeled
  request-local base64 image URL.
- Targeted suite: 26 passed.

## Known issue

The full unit suite has five pre-existing failures from the separately landed
screen-capture platform work: four tests still expect `bundle.screen is None`,
and the persistence invariant currently flags Pillow's in-memory
`BytesIO.save()` call. This task does not modify those platform files.

## Fix follow-up

Commit `f5afea8` — updated platform bundle tests to expect wired screen capture
(`MacScreenCapture` on macOS, `UnsupportedScreenCapture` on linux/windows) and
refined the ScreenFrame persistence invariant to ignore in-memory Pillow
`save()` calls targeting `BytesIO` sinks while still flagging path-like writes.

Tests: targeted suite 27 passed; full `tests/unit` 1000 passed, 1 skipped.
