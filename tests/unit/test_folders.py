from pathlib import Path

from vaani.folders import resolve_folder, resolve_folder_name, launch_folder


def test_resolve_common_folders():
    assert resolve_folder("open downloads").name == "Downloads"
    assert resolve_folder("Downloads folder kholo").name == "Downloads"
    assert resolve_folder("open my documents").name == "Documents"
    assert resolve_folder("open home folder").name == "Home"
    assert resolve_folder("desktop folder dikhao").name == "Desktop"
    assert resolve_folder_name("pictures").name == "Pictures"


def test_folder_paths_under_home():
    target = resolve_folder("open downloads")
    assert target is not None
    assert target.path == Path.home() / "Downloads"


def test_launch_folder_uses_xdg_open(monkeypatch, tmp_path):
    seen = []

    def fake_popen(argv, **kwargs):
        seen.append(argv)
        return None

    monkeypatch.setattr("vaani.folders.subprocess.Popen", fake_popen)
    from vaani.folders import FolderTarget

    target = FolderTarget("Downloads", tmp_path)
    assert launch_folder(target).startswith("Opened")
    assert seen and seen[0][0] == "xdg-open" and seen[0][1] == str(tmp_path)
