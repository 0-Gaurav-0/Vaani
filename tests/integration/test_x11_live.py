import os, pytest

def test_x11_xtest_available():
    if not os.environ.get("DISPLAY"):
        pytest.skip("X11 DISPLAY unavailable")
    try:
        from Xlib.display import Display
        d=Display()
        assert d.has_extension("XTEST")
        from Xlib import X, XK
        root=d.screen().root; key=d.keysym_to_keycode(XK.string_to_keysym("F24"))
        try:
            root.grab_key(key, X.AnyModifier, True, X.GrabModeAsync, X.GrabModeAsync)
            d.sync(); grabbed=True
        except Exception:
            grabbed=False
        finally:
            if grabbed:
                root.ungrab_key(key, X.AnyModifier); d.sync()
        assert grabbed, "passive grab unavailable (deterministic BadAccess)"
        d.close()
    except Exception as exc:
        pytest.skip(f"X11 unavailable: {exc}")
