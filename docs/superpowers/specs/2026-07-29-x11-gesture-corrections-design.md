# X11 Gesture Corrections

**Date:** 2026-07-29
**Scope:** Vaani middle-button routing, state-aware GNOME actions, and the current
X11 Touchégg configuration

## Goal

Make the existing gestures behave reliably without changing the X11 session:

- Three-finger touchpad tap toggles global media play/pause and never starts
  Vaani.
- Physical TrackPoint middle-button hold/release continues to control Vaani.
- Three-finger horizontal swipes traverse the application switcher in the
  direction the fingers move.
- Four-finger up and down are reciprocal across normal, Overview, and
  show-desktop states.
- Existing pinch zoom, volume, and four-finger workspace switching remain
  intact.

## Constraints

- Keep X11, Touchégg, and the currently working Vaani core middle-button grab.
- Do not retry the XInput2 passive-grab implementation that disabled the live
  TrackPoint hotkey.
- Do not change Time Doctor, Sprinto, Tailscale, touchpad tapping configuration,
  or the disabled GNOME X11 Gestures extension.
- Treat the live device topology as one enabled `Elan Touchpad`, one enabled
  `Elan TrackPoint`, and XTEST virtual devices. If that physical topology
  changes, media routing must fail closed rather than treating an unknown
  pointer as the touchpad.

## Middle-Button Routing

Retain the existing Vaani manager's proven global core button-2 grab, TrackPoint
resolution, button-scrolling lifecycle, hold/double-press state machine, and Esc
handling.

At registration:

1. Resolve the physical TrackPoint by its exact known name as today.
2. Verify that the enabled physical slave-pointer topology contains exactly the
   expected Elan touchpad and TrackPoint. Ignore XTEST virtual devices.
3. Enable touchpad-media routing only while that topology is verified.

For each grabbed button-2 press:

1. If an earlier press is still latched, ignore the duplicate.
2. Query the resolved physical TrackPoint's button-2 state.
3. If the state is down, latch the press as TrackPoint and enter only the
   existing `MiddleButtonGesture`.
4. If the state is up, reverify the physical pointer topology. If it still
   matches, latch the press as a touchpad media tap. Do not touch Vaani gesture
   or session state.
5. If state or topology cannot be verified, latch nothing and perform no
   action.

For release:

- A latched TrackPoint release follows the existing Vaani release path.
- A latched touchpad release invokes the media callback exactly once.
- An unknown or unmatched release does nothing except clear stale safety
  latches.
- Touchpad routing never calls Vaani trigger, release, or cancel callbacks.
- Esc cancellation continues to ignore a held TrackPoint until its release, but
  Esc while idle does not disable later presses.

This design deliberately extends the currently working manager rather than
changing its grab architecture.

## Media Key Delivery

Add a focused XTEST media-key sender and inject it into the Linux hotkey manager.
Resolve `XK_XF86_AudioPlay` from the XF86 keysym constant to the current X11
keycode; do not hard-code keycode 172 or use string lookup.

Emit one key press and release, attempting release in `finally`, then synchronize
the display. A delivery failure is logged and remains isolated from Vaani.
Touchégg has no three-finger `TAP` entry because its tap gesture applies to
touchscreens, not this touchpad.

## Three-Finger Horizontal Swipes

Keep repeated key delivery with three progress steps and preserve reversal within
one gesture, but swap the forward/backward actions:

- Left: hold Alt, repeat Shift+Tab, use Tab when reversing.
- Right: hold Alt, repeat Tab, use Shift+Tab when reversing.

Touchégg presses Alt at gesture start, emits a switcher step as progress
increases, and releases Alt when the gesture ends.

## State-Aware Four-Finger Up and Down

Replace the independent static up/down actions with non-repeating Touchégg
`RUN_COMMAND` actions executed at gesture end. Both call a small Linux/X11
helper in `src/vaani/platform/linux/desktop_gestures.py` with an `up` or `down`
argument. The active Touchégg config invokes that module through the live Vaani
checkout's virtual-environment Python.

The helper reads live state rather than maintaining a state file:

- GNOME Overview: `org.gnome.Shell.OverviewActive` over the session D-Bus.
- Desktop visibility: root-window `_NET_SHOWING_DESKTOP`.

The transition table is:

| Current state | Four-finger up | Four-finger down |
| --- | --- | --- |
| Normal application view | Open Overview | Show desktop |
| Overview open | Close Overview | Close Overview |
| Desktop showing | Restore windows | Restore windows |

Up therefore remains a true Super-style Overview toggle except that restoring a
shown desktop takes priority. Down always returns toward the application view
when Overview is open, and otherwise toggles show-desktop.

GNOME normally keeps Overview and show-desktop mutually exclusive. If both flags
are unexpectedly true, up restores the desktop first and down closes Overview
first; the other flag is left unchanged.

The helper sets `OverviewActive` through D-Bus and sends the standard
`_NET_SHOWING_DESKTOP` X11 client message. It does not minimize or close windows
individually, so GNOME retains their workspace, stacking, and layout.

Four-finger left/right remain the existing animated `CHANGE_DESKTOP` actions.

## Failure Handling

- TrackPoint state unknown: do not start Vaani or toggle media.
- Unexpected physical pointer topology: keep TrackPoint behavior available but
  disable touchpad-media classification.
- Media keysym or XTEST failure: log the error, attempt key release, and do not
  fall through to Vaani.
- Overview-state query or D-Bus mutation failure: log and leave desktop state
  unchanged.
- `_NET_SHOWING_DESKTOP` query or message failure: log and leave windows
  unchanged.
- Helper failure must not affect Vaani, Touchégg's other gestures, or window
  contents.
- Shutdown restores the TrackPoint scrolling property and clears all routing
  latches.

## Tests

Add focused unit coverage for:

- Touchpad press/release toggles media once and never triggers Vaani.
- TrackPoint hold/release, double-press, and Esc behavior remain unchanged.
- Unknown state and unexpected physical pointer topology fail closed for media.
- Duplicate and unmatched events do not cross the Vaani/media paths.
- Unregister restores button scrolling and clears routing latches.
- The XTEST media sender resolves the XF86 constant and attempts key release
  after failure.
- Every state/direction pair in the four-finger transition table.
- D-Bus and EWMH failures cause no fallback transition.
- Exact Touchégg XML direction, repeat, reversal, command, and preserved-gesture
  semantics.

## Live Validation

1. Run focused hotkey, desktop-helper, XML, and relevant Vaani unit tests.
2. Confirm Vaani is idle from its latest lifecycle event and that no recorder or
   indicator is active.
3. Let Touchégg reload the configuration and confirm successful parsing.
4. Restart only Vaani.
5. Verify physical TrackPoint middle hold/release starts and stops Vaani before
   testing touchpad media.
6. With a live log monitor, confirm a three-finger touchpad tap toggles
   background or minimized media exactly once and creates no Vaani recording
   event.
7. Confirm left moves backward with Alt+Shift+Tab, right moves forward with
   Alt+Tab, a long swipe crosses at least five entries, reversal works, and Alt
   is released after lift.
8. Exercise every row of the four-finger transition table and confirm windows
   return with the same layout.
9. Confirm volume, workspace, and pinch gestures still work.
10. Do not declare completion until TrackPoint and touchpad behavior are both
    observed on the real hardware.
