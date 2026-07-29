# X11 Gesture Corrections

**Date:** 2026-07-29
**Scope:** Vaani middle-button input routing and the current X11 Touchégg configuration

## Goal

Make the existing gestures behave reliably without changing the X11 session:

- Three-finger touchpad tap toggles global media play/pause and never starts Vaani.
- Physical TrackPoint middle-button hold/release continues to control Vaani.
- A single three-finger horizontal swipe can traverse multiple Alt+Tab entries.
- Four-finger up toggles GNOME Activities Overview.
- Four-finger down shows the current workspace desktop without closing windows; repeating it restores them.
- Existing pinch zoom, volume, and four-finger workspace switching remain intact.

## Input Architecture

The current manager receives source-blind core button-2 events and infers their
origin by querying device state afterward. That introduces a race for short
touchpad taps. Replace that inference with XInput2 device-specific passive grabs.

At registration:

1. Query XInput2 devices and resolve exactly one enabled `Elan Touchpad` slave
   pointer and one enabled TrackPoint slave pointer by exact known name.
2. Require distinct device IDs. Missing, ambiguous, disabled, or reused IDs fail
   closed.
3. Install button-2 passive grabs separately for the touchpad and TrackPoint,
   using asynchronous modes, any modifier, and button press/release events.
4. Keep the existing TrackPoint button-scrolling disable/restore lifecycle.

Route events by the XInput2 source device ID:

- TrackPoint press/release enters only the existing `MiddleButtonGesture`
  hold/double-press state machine.
- A matched touchpad press/release invokes the media callback exactly once on
  release and never touches Vaani gesture state.
- Unknown sources and unmatched releases do nothing.
- Touchpad input during a TrackPoint hold cannot stop Vaani.
- TrackPoint input cannot toggle media.

If device identity or grab setup becomes invalid, release any active Vaani
session safely, remove stale grabs, re-resolve the exact devices, and reinstall
grabs only after successful verification. Do not fall back to a core global grab,
an arbitrary pointer, a hard-coded device ID, or state polling.

## Media Key Delivery

Add a focused XTEST media-key sender and inject it into the Linux hotkey manager.
Resolve `XK_XF86_AudioPlay` from the XF86 keysym constant to the current X11
keycode; do not hard-code keycode 172 or use string lookup.

Emit one key press and release, attempting release in `finally`, then synchronize
the display. A delivery failure is logged and remains isolated from Vaani.
Remove the Touchégg `TAP` entry because Touchégg tap gestures are touchscreen-only.

## Touchégg Configuration

Keep two-finger pinch, three-finger volume, and four-finger left/right workspace
actions unchanged.

For three-finger horizontal swipes, preserve the current direction behavior but
use repeated key delivery with ten progress steps:

- Left: hold Alt, repeat Tab, use Shift+Tab when reversing.
- Right: hold Alt, repeat Shift+Tab, use Tab when reversing.

Touchégg presses Alt at gesture start, emits a Tab action at each progress step,
and releases Alt when the gesture ends.

Add:

- Four-finger up: non-repeating bare `Super_L`, executed at gesture end.
- Four-finger down: native animated `SHOW_DESKTOP`.

`SHOW_DESKTOP` uses `_NET_SHOWING_DESKTOP`. On GNOME 42 it applies to the current
workspace and is reversible; it does not close windows or permanently minimize
windows across every virtual workspace.

Leave the installed X11 Gestures GNOME extension disabled. Enabling it with its
stored three-finger setting would compete with the custom three-finger actions.

## Failure Handling

- XInput2 unavailable or device resolution/grab failure: neither Vaani nor media
  is triggered from an unverified source.
- Media keysym unavailable or XTEST failure: log the error, attempt key release,
  and do not fall through to Vaani.
- Device removal or ID reuse: invalidate both latches and grabs before
  re-resolution.
- Shutdown or partial setup failure: remove installed grabs and restore the
  TrackPoint scrolling property.

## Tests

Add focused unit coverage for:

- Touchpad press/release toggles media once and never triggers Vaani.
- TrackPoint hold/release and double-press behavior remain unchanged.
- Touchpad input during a TrackPoint hold cannot release Vaani.
- Cross-device and unmatched releases do nothing.
- Unknown, missing, ambiguous, disabled, or reused devices fail closed.
- Partial passive-grab failure cleans up completed setup.
- Unregister restores button scrolling and clears latches.
- The XTEST media sender resolves the XF86 constant and attempts key release
  after failure.

Validate the XML structure and exact gesture semantics after editing.

## Live Validation

1. Run focused hotkey tests and the relevant Vaani unit suite.
2. Confirm Vaani is idle from a terminal lifecycle event and no capture process
   or indicator is active.
3. Allow Touchégg to reload its configuration; confirm successful parsing.
4. Restart only Vaani and confirm exact device IDs/grabs in the log.
5. With a read-only XInput/log monitor active, perform real gestures:
   - Three-finger tap toggles background/minimized media exactly once, produces
     no Vaani recording event, and sends no middle click to the focused app.
   - TrackPoint middle hold/release starts/stops Vaani without toggling media.
   - One long horizontal swipe traverses at least five switcher entries; reversing
     before lift moves backward; Alt is released after lift.
   - Four-finger up toggles Overview.
   - Four-finger down shows the current workspace desktop and repeating it restores
     the windows.
   - Existing volume, workspace, and pinch gestures still work.
6. Do not declare completion until the real hardware behavior is observed.

Do not change X11 configuration, Time Doctor, Sprinto, Tailscale, unrelated
files, or the disabled GNOME X11 Gestures extension.
