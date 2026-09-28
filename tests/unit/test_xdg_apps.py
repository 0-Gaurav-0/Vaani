import json
from pathlib import Path

from vaani.apps import load_user_apps, resolve_app_name
from vaani.xdg_apps import (
    DesktopApp,
    clear_desktop_cache,
    launch_desktop_app,
    load_desktop_apps,
    resolve_desktop_app,
)


def test_load_user_apps_json(tmp_path):
    path = tmp_path / "apps.json"
    path.write_text(
        json.dumps(
            {
                "apps": [
                    {
                        "name": "Signal",
                        "aliases": ["signal", "signal app"],
                        "executables": ["signal-desktop"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    loaded = load_user_apps(path)
    assert loaded and loaded[0][1].name == "Signal"
    assert "signal" in loaded[0][0]


def test_load_user_apps_invalid_fails_closed(tmp_path):
    path = tmp_path / "apps.json"
    path.write_text("{not-json", encoding="utf-8")
    assert load_user_apps(path) == ()


def test_resolve_desktop_app_from_fixture(tmp_path, monkeypatch):
    apps_dir = tmp_path / "applications"
    apps_dir.mkdir()
    desktop = apps_dir / "cool-editor.desktop"
    desktop.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Cool Editor\n"
        "GenericName=Text IDE\n"
        "Exec=/usr/bin/cool-editor %U\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "vaani.xdg_apps._desktop_dirs", lambda: (apps_dir,)
    )
    clear_desktop_cache()
    hit = resolve_desktop_app("open cool editor")
    assert hit is not None
    assert hit.name == "Cool Editor"
    assert hit.desktop_id == "cool-editor"
    clear_desktop_cache()


def test_launch_desktop_prefers_gtk_launch(monkeypatch):
    seen = []

    monkeypatch.setattr("vaani.xdg_apps.shutil.which", lambda name: "/usr/bin/gtk-launch" if name == "gtk-launch" else None)

    def fake_popen(argv, **kwargs):
        seen.append(argv)
        return None

    monkeypatch.setattr("vaani.xdg_apps.subprocess.Popen", fake_popen)
    app = DesktopApp("Cool", "cool-editor", ("/usr/bin/cool-editor",), ("cool",))
    assert launch_desktop_app(app).startswith("Opened")
    assert seen[0][:2] == ["/usr/bin/gtk-launch", "cool-editor"]
