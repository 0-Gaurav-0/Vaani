"""Reuse an existing YouTube browser tab instead of stacking new ones."""
from __future__ import annotations

import logging
import subprocess
import time
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger("vaani.youtube_tab")


def _is_youtube_url(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").casefold()
    except Exception:
        return False
    return host.endswith("youtube.com") or host.endswith("youtu.be")


def _clipboard_set(text: str) -> bool:
    try:
        subprocess.run(
            ["xclip", "-selection", "clipboard"],
            input=text.encode("utf-8"),
            check=True,
            timeout=2.0,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def _find_youtube_window(dpy: Any) -> Any | None:
    """Return an X window whose title looks like YouTube, or None."""
    from Xlib import X
    from Xlib.display import Display

    root = dpy.screen().root
    net_client = dpy.intern_atom("_NET_CLIENT_LIST")
    wm_name = dpy.intern_atom("_NET_WM_NAME")
    wm_name_legacy = dpy.intern_atom("WM_NAME")
    utf8 = dpy.intern_atom("UTF8_STRING")

    try:
        prop = root.get_full_property(net_client, X.AnyPropertyType)
    except Exception:
        return None
    if prop is None:
        return None
    best = None
    for wid in prop.value:
        try:
            win = dpy.create_resource_object("window", wid)
        except Exception:
            continue
        title = ""
        try:
            p = win.get_full_property(wm_name, utf8)
            if p and p.value:
                title = (
                    p.value.decode("utf-8", "replace")
                    if isinstance(p.value, bytes)
                    else str(p.value)
                )
        except Exception:
            title = ""
        if not title:
            try:
                p = win.get_full_property(wm_name_legacy, X.AnyPropertyType)
                if p and p.value:
                    title = (
                        p.value.decode("utf-8", "replace")
                        if isinstance(p.value, (bytes, bytearray))
                        else str(p.value)
                    )
            except Exception:
                continue
        folded = title.casefold()
        if "youtube" in folded:
            # Prefer watch pages over the bare home tab when possible.
            if " - youtube" in folded or "youtu.be" in folded:
                return win
            best = best or win
    return best


def _activate_window(dpy: Any, win: Any) -> None:
    from Xlib import X

    root = dpy.screen().root
    atom = dpy.intern_atom("_NET_ACTIVE_WINDOW")
    try:
        from Xlib.protocol import event

        ev = event.ClientMessage(
            window=win,
            client_type=atom,
            data=(32, [2, X.CurrentTime, 0, 0, 0]),
        )
        mask = X.SubstructureRedirectMask | X.SubstructureNotifyMask
        root.send_event(ev, event_mask=mask)
        dpy.sync()
    except Exception:
        try:
            win.set_input_focus(X.RevertToParent, X.CurrentTime)
            win.configure(stack_mode=X.Above)
            dpy.sync()
        except Exception:
            pass


def _tap_combo(dpy: Any, fake_input: Any, *keysyms: str) -> None:
    from Xlib import X, XK

    codes = []
    for name in keysyms:
        ks = XK.string_to_keysym(name)
        code = int(dpy.keysym_to_keycode(ks) or 0)
        if not code:
            raise RuntimeError(f"missing keysym {name}")
        codes.append(code)
    try:
        for code in codes:
            fake_input(dpy, X.KeyPress, code)
        for code in reversed(codes):
            fake_input(dpy, X.KeyRelease, code)
    finally:
        dpy.sync()


def youtube_shortcut(*keysyms: str) -> bool:
    """Focus a YouTube window and tap a keyboard shortcut (e.g. Shift+n)."""
    if not keysyms:
        return False
    try:
        from Xlib.display import Display
        from Xlib.ext import xtest
    except Exception:
        return False
    dpy = Display()
    try:
        win = _find_youtube_window(dpy)
        if win is None:
            logger.info("event=youtube_shortcut miss=no_window keys=%s", "+".join(keysyms))
            return False
        _activate_window(dpy, win)
        time.sleep(0.18)
        # Clear address-bar / search focus so player shortcuts land.
        _tap_combo(dpy, xtest.fake_input, "Escape")
        time.sleep(0.05)
        _tap_combo(dpy, xtest.fake_input, *keysyms)
        logger.info("event=youtube_shortcut ok=1 keys=%s", "+".join(keysyms))
        return True
    except Exception as exc:
        logger.info(
            "event=youtube_shortcut_failed detail=%s", type(exc).__name__
        )
        return False
    finally:
        try:
            dpy.close()
        except Exception:
            pass


def navigate_youtube_tab(url: str) -> bool:
    """Load ``url`` in an existing YouTube tab.

    Prefers Brave DevTools (works minimized). Falls back to focus+paste.
    """
    if not _is_youtube_url(url):
        return False
    try:
        from .brave_cdp import navigate_youtube_url

        if navigate_youtube_url(url, autoplay=True):
            logger.info("event=youtube_tab_reuse ok=1 via=cdp")
            return True
    except Exception as exc:
        logger.info(
            "event=youtube_tab_cdp_skipped detail=%s", type(exc).__name__
        )

    if not _clipboard_set(url):
        return False
    try:
        from Xlib.display import Display
        from Xlib.ext import xtest
    except Exception:
        return False

    dpy = Display()
    try:
        win = _find_youtube_window(dpy)
        if win is None:
            logger.info("event=youtube_tab_reuse miss=no_window")
            return False
        _activate_window(dpy, win)
        time.sleep(0.15)
        fake = xtest.fake_input
        # Select address bar → paste → go.
        _tap_combo(dpy, fake, "Control_L", "l")
        time.sleep(0.08)
        _tap_combo(dpy, fake, "Control_L", "v")
        time.sleep(0.08)
        _tap_combo(dpy, fake, "Return")
        time.sleep(1.4)
        # Keyboard navigate often lands on the page without autoplay.
        try:
            from .brave_cdp import ensure_youtube_playing, navigate_youtube_url

            if not ensure_youtube_playing():
                navigate_youtube_url(url, autoplay=True)
        except Exception:
            _tap_combo(dpy, fake, "k")
        logger.info("event=youtube_tab_reuse ok=1 via=keys")
        return True
    except Exception as exc:
        logger.info(
            "event=youtube_tab_reuse_failed detail=%s", type(exc).__name__
        )
        return False
    finally:
        try:
            dpy.close()
        except Exception:
            pass
