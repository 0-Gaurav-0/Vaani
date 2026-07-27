# Task 8 — Guide pack verbs (V1)

## Delivered

- Added the disabled-by-default `guide` pack with R0 `guide.point`,
  `guide.offer`, and `guide.last_result` verbs.
- `guide.point` captures an in-memory screen frame, calls the injected guide
  brain, maps validated screenshot coordinates to global display coordinates,
  and writes the resulting ops to the injected overlay.
- Added grammar for screen pointers and safe how-to offers. When the guide pack
  is enabled, “how do I free port 3000” routes to `guide.offer`, not the R2
  port-free action.
- Added unsupported handling for unavailable screen, overlay, or brain
  dependencies; `guide.last_result` safely reports when no overlay exists.

## Tests

- Added `tests/unit/verbs/test_guide_pack.py` using `FakeScreen`, a fake brain,
  and `FakeOverlay`.
- Full unit suite: `1006 passed, 1 skipped`.

## Notes

- The guide pack remains disabled by default. Runtime screen/overlay/brain
  wiring is intentionally deferred to Task 10.

## Task 8 review fix — guide.offer you/we phrasing

- Expanded `guide.offer` `any_of` to include `how do/can/would/should you/we`
  variants, aligned with the slot regex `(?:i|you|we)`.
- Normalization no longer strips `can you` / `would you` when preceded by
  `how`, so `"how can you free port 3000"` survives matching intact.
- Added router test `test_enabled_guide_offer_matches_how_can_you_phrasing`.

```text
$ .venv/bin/python -m pytest tests/unit/verbs/test_guide_pack.py -q
.......                                                                  [100%]
7 passed in 0.03s
```

Commit: `b2e0e83` — `fix(guide): match how can you/we phrases for guide.offer`
