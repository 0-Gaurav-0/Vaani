from vaani.paste_target import needs_shift_paste


def test_needs_shift_paste_terminal_and_ide():
    assert needs_shift_paste(wm_class="gnome-terminal-server", a11y_terminal=False) is True
    assert needs_shift_paste(wm_class="Cursor", a11y_terminal=True) is True
    assert needs_shift_paste(wm_class="Cursor", a11y_terminal=False) is False
    assert needs_shift_paste(wm_class="firefox", a11y_terminal=True) is False
