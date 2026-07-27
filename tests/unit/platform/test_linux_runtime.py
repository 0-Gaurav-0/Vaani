from vaani.platform.linux.runtime import _detect_display_backend

def test_defaults_to_x11_with_no_session_hints():
    assert _detect_display_backend({}) == "x11"

def test_wayland_display_env_selects_wayland():
    assert _detect_display_backend({"WAYLAND_DISPLAY": "wayland-0"}) == "wayland"

def test_xdg_session_type_wayland_selects_wayland():
    assert _detect_display_backend({"XDG_SESSION_TYPE": "wayland"}) == "wayland"

def test_xdg_session_type_wayland_is_case_insensitive():
    assert _detect_display_backend({"XDG_SESSION_TYPE": "Wayland"}) == "wayland"

def test_x11_session_with_display_stays_x11():
    assert _detect_display_backend({"DISPLAY": ":0", "XDG_SESSION_TYPE": "x11"}) == "x11"

def test_wayland_display_wins_over_x11_session_type():
    assert _detect_display_backend({"WAYLAND_DISPLAY": "wayland-0", "XDG_SESSION_TYPE": "x11"}) == "wayland"
