"""Focus adapters against fakes (T3.2)."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from vaani.context import (
    ActiveFocus,
    FakeFocusProbe,
    NullFocusProbe,
    build_context,
    clear_last_used,
    resolve_focus,
)
from vaani.context.focus import classify_role, project_root_from_document
from vaani.intent.schema import FocusInfo, Support
from vaani.platform.linux.focus import LinuxFocusProbe
from vaani.platform.macos.focus import MacOSFocusProbe
from vaani.platform.protocol import PlatformId
from vaani.platform.windows.focus import WindowsFocusProbe


def setup_function() -> None:
    clear_last_used()


def teardown_function() -> None:
    clear_last_used()


def test_classify_role_editor_and_terminal() -> None:
    assert classify_role("com.todesktop.230313mzl4w4u92", "Cursor") == "editor"
    assert classify_role("Code", "Visual Studio Code") == "editor"
    assert classify_role("Terminal", "zsh — vaani") == "terminal"
    assert classify_role("kitty", None) == "terminal"
    assert classify_role("Safari", "Apple") == "other"


def test_project_root_from_document(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    nested = root / "src" / "pkg"
    nested.mkdir(parents=True)
    (root / ".git").mkdir()
    doc = nested / "main.py"
    doc.write_text("x\n", encoding="utf-8")
    assert project_root_from_document(doc) == root.resolve()


def test_fake_focus_probe_enrichment(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    doc = root / "a.py"
    doc.write_text("pass\n", encoding="utf-8")
    probe = FakeFocusProbe(
        ActiveFocus(
            info=FocusInfo(app_id="Cursor", document_path=doc),
            role="other",
        )
    )
    active = resolve_focus(probe)
    assert active.role == "editor"
    assert active.project_root == root.resolve()


def test_null_focus_probe() -> None:
    active = NullFocusProbe().probe()
    assert active.info is None
    assert active.support is Support.SUPPORTED


def test_macos_focus_probe_with_fake_runner(tmp_path: Path) -> None:
    doc = tmp_path / "file.py"
    doc.write_text("x\n", encoding="utf-8")
    (tmp_path / ".git").mkdir()

    def runner(args, **_kwargs):
        script = args[-1]
        if "AXDocument" in script:
            return SimpleNamespace(returncode=0, stdout=str(doc))
        return SimpleNamespace(returncode=0, stdout="Cursor, com.todesktop.cursor")

    active = MacOSFocusProbe(runner=runner).probe()
    assert active.support is Support.SUPPORTED
    assert active.info is not None
    assert active.info.app_id == "com.todesktop.cursor"
    assert active.role == "editor"
    assert active.project_root == tmp_path.resolve()


def test_windows_focus_probe_with_fake_win32(tmp_path: Path) -> None:
    class _Buf:
        def __init__(self, value: str = "") -> None:
            self.value = value

    class _Fake:
        class ctypes:
            @staticmethod
            def byref(obj):
                return obj

        class wintypes:
            class DWORD:
                def __init__(self, value: int = 0) -> None:
                    self.value = value

        @staticmethod
        def create_unicode_buffer(n: int) -> _Buf:
            return _Buf("Cursor — file.py")

        class user32:
            @staticmethod
            def GetForegroundWindow() -> int:
                return 42

            @staticmethod
            def GetWindowTextLengthW(_hwnd: int) -> int:
                return 10

            @staticmethod
            def GetWindowTextW(_hwnd, buf, _n) -> None:
                buf.value = "Cursor — file.py"

            @staticmethod
            def GetWindowThreadProcessId(_hwnd, pid) -> None:
                pid.value = 7

        class kernel32:
            @staticmethod
            def OpenProcess(_access, _inherit, _pid) -> int:
                return 0

    active = WindowsFocusProbe(
        win32=_Fake(),
        uia_document=lambda: tmp_path / "doc.py",
    ).probe()
    assert active.support is Support.SUPPORTED
    assert active.info is not None
    assert active.role == "editor"


def test_linux_x11_focus_probe_fake() -> None:
    class _Probe:
        def active_window(self) -> int:
            return 99

    def meta(_probe, window_id: int) -> tuple[str | None, str | None]:
        assert window_id == 99
        return "Code", "vaani — Visual Studio Code"

    active = LinuxFocusProbe(
        environ={"XDG_SESSION_TYPE": "x11"},
        x11_probe=_Probe(),
        window_meta=meta,
    ).probe()
    assert active.support is Support.SUPPORTED
    assert active.role == "editor"
    assert active.info is not None
    assert active.info.app_id == "Code"


def test_linux_wayland_unsupported() -> None:
    active = LinuxFocusProbe(environ={"XDG_SESSION_TYPE": "wayland"}).probe()
    assert active.support is Support.UNSUPPORTED
    assert active.info is None
    assert "Wayland" in active.reason


def test_build_context_uses_editor_focus(tmp_path: Path) -> None:
    clear_last_used()
    root = tmp_path / "ws"
    root.mkdir()
    (root / ".git").mkdir()
    home = tmp_path / "home"
    home.mkdir()
    probe = FakeFocusProbe(
        ActiveFocus(
            info=FocusInfo(app_id="Cursor", document_path=root / "a.py"),
            role="editor",
            project_root=root,
        )
    )

    def fake_runner(cmd):
        from dataclasses import dataclass

        @dataclass(frozen=True)
        class _Done:
            argv: tuple[str, ...]
            returncode: int
            stdout: str
            stderr: str
            timed_out: bool = False
            cancelled: bool = False

        argv = cmd.argv
        if "--show-toplevel" in argv:
            return _Done(
                argv=argv, returncode=0, stdout=str(root.resolve()) + "\n", stderr=""
            )
        if "--abbrev-ref" in argv and "HEAD" in argv:
            return _Done(argv=argv, returncode=0, stdout="main\n", stderr="")
        if "--porcelain" in argv:
            return _Done(argv=argv, returncode=0, stdout="", stderr="")
        return _Done(argv=argv, returncode=1, stdout="", stderr="")

    ctx = build_context(
        PlatformId.MACOS,
        focus_probe=probe,
        runner=fake_runner,
        environ={},
        home=home,
        last_used=None,
        remember=False,
    )
    assert ctx.workspace == root.resolve()
    assert ctx.workspace_source == "focused-editor"
    assert ctx.focus is not None
    assert ctx.repo is not None
    assert ctx.repo.root == root.resolve()
    assert ctx.repo.branch == "main"
