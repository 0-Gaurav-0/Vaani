from vaani.paste_target import (
    is_terminal_wm_class,
    needs_shift_paste,
    title_suggests_ide_terminal,
)


def test_needs_shift_paste_terminal_and_ide():
    assert needs_shift_paste(wm_class="gnome-terminal-server", a11y_terminal=False) is True
    assert needs_shift_paste(wm_class="Cursor", a11y_terminal=True) is True
    assert needs_shift_paste(wm_class="Cursor", a11y_terminal=False) is False
    assert needs_shift_paste(wm_class="firefox", a11y_terminal=True) is False


def test_warp_wm_class_uses_shift_paste():
    assert is_terminal_wm_class("dev.warp.Warp dev.warp.Warp") is True
    assert needs_shift_paste(wm_class="dev.warp.Warp", a11y_terminal=False) is True


def test_ide_title_suggests_terminal():
    assert title_suggests_ide_terminal("bash") is True
    assert title_suggests_ide_terminal("Vaani — bash") is True
    assert title_suggests_ide_terminal("project — Terminal") is True
    assert title_suggests_ide_terminal("paste_target.py — Vaani") is False
    assert needs_shift_paste(
        wm_class="cursor",
        window_title="Vaani — bash",
        a11y_terminal=False,
    ) is True
