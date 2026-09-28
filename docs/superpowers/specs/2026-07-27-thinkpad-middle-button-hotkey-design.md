# Middle-button hold-to-talk (ThinkPad)

**Date:** 2026-07-27  
**Status:** Approved  
**Scope:** Linux/X11 ThinkPad middle-button trigger; release Ctrl+Space back to the system

## Goal

Vaani is started only from the ThinkPad **middle button** (pointer button 2). Keyboard chords are no longer used for dictation/assistant, so Ctrl+Space returns to normal desktop/IME use. Function/media keys must remain untouched.

## Triggers

| Gesture | Mode |
|---|---|
| Single press + hold | Smart dictation |
| Double-press + hold | Assistant |
| Esc | Cancel (unchanged) |
| Short single click (down/up before hold threshold, no second press) | No-op (button fully owned) |

Literal mode is out of scope for this change.

### Timing

- **Hold threshold:** ~150ms — after first press, if still held and no intervening release, start **smart**.
- **Double window:** ~350ms after a short click release — a second press starts **assistant** immediately and continues while held.
- Release ends the active session (same hold-to-talk contract as today).

## Ownership (option 1)

While Vaani is running:

1. Claim pointer **button 2** so apps do not receive middle-click / middle-paste.
2. Disable TrackPoint **button scrolling** (`libinput` scroll-on-button-2); restore previous setting on shutdown.
3. Accept that TrackPoint-middle scroll and middle-paste are unavailable until Vaani exits.

## Non-goals

- Do not grab F-keys, ThinkPad Extra Buttons, or volume/mute keys.
- Do not keep Ctrl+Space / Ctrl+Shift+Space / Ctrl+Alt+Space as Vaani triggers.
- No Wayland-specific portal redesign in this change (X11 path first; Wayland keeps existing portal chords unless extended later).

## Implementation sketch

- Replace Linux X11 `XInputHotkeyManager` keyboard chord polling with a **middle-button manager** that:
  - Resolves the TrackPoint (fallback: first pointer that reports button 2),
  - Disables button-2 delivery (button map and/or scroll prop),
  - Polls `xinput query-state` for `button[2]=down` (same poll style as today’s reliable hold detection),
  - Runs the gesture state machine above,
  - Still watches **Escape** on the keyboard device only (read-only query-state; no Space/Ctrl chord).
- Wire `platform/linux/runtime.py` to this manager; update startup banner text.
- Unit-test the gesture state machine with fake clock/button events.

## Fn / media keys

Vaani must not register grabs on `ThinkPad Extra Buttons` or XF86 media keys. Mic remains open only while middle is held. If Fn keys still fail after this ships with Vaani stopped, treat as GNOME/Fn-lock outside this spec.

## Success criteria

1. Holding middle once starts dictation; release stops.
2. Click-click-hold starts assistant; release stops.
3. Ctrl+Space no longer starts Vaani.
4. No `parec` while idle; Fn devices stay enabled.
5. Scroll/button-scroll props restored after clean shutdown.
