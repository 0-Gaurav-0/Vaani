"""Known-folder resolution (T1.3 files.open_dir)."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from vaani.known_folders import canonical_folder, resolve_known_folder
from vaani.platform.protocol import PlatformId


def test_canonical_folder_aliases() -> None:
    assert canonical_folder("Downloads") == "downloads"
    assert canonical_folder("docs") == "documents"
    assert canonical_folder("not-a-folder") is None


def test_linux_uses_xdg_user_dir_not_home_string_build() -> None:
    calls: list[list[str]] = []

    def runner(argv, **_kwargs):
        calls.append(list(argv))
        return SimpleNamespace(returncode=0, stdout="/srv/downloads\n", stderr="")

    path = resolve_known_folder(
        "Downloads",
        PlatformId.LINUX,
        runner=runner,
        which=lambda name: "/usr/bin/xdg-user-dir" if name == "xdg-user-dir" else None,
        getenv=lambda _k: None,
        home=Path("/home/user"),
    )
    assert path == Path("/srv/downloads")
    assert calls == [["xdg-user-dir", "DOWNLOAD"]]
    assert "~" not in str(path)
    assert str(path) != str(Path("/home/user") / "Downloads")


def test_linux_falls_back_to_xdg_env() -> None:
    path = resolve_known_folder(
        "Desktop",
        PlatformId.LINUX,
        runner=lambda *_a, **_k: SimpleNamespace(returncode=1, stdout="", stderr=""),
        which=lambda _name: None,
        getenv=lambda key: "/xdg/Desktop" if key == "XDG_DESKTOP_DIR" else None,
        home=Path("/home/user"),
    )
    assert path == Path("/xdg/Desktop")


def test_macos_uses_osascript_path_to_folder() -> None:
    def runner(argv, **_kwargs):
        assert argv[:2] == ["osascript", "-e"]
        assert "path to downloads folder" in argv[2]
        return SimpleNamespace(returncode=0, stdout="/Users/me/Downloads/\n", stderr="")

    path = resolve_known_folder(
        "downloads",
        PlatformId.MACOS,
        runner=runner,
        home=Path("/Users/me"),
    )
    assert path == Path("/Users/me/Downloads")
