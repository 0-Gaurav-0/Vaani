from types import SimpleNamespace

from vaani.apps import AppTarget, launch_app, resolve_app


def test_resolves_desktop_app_aliases():
    assert resolve_app("Open Claude").name == "Claude"
    assert resolve_app("Launch the Terminal").name == "Terminal"
    assert resolve_app("Start Text Editor").name == "Text Editor"
    assert resolve_app("Open VS Code").name == "Visual Studio Code"
    assert resolve_app("Open vscode").name == "Visual Studio Code"
    assert resolve_app("Open code").name == "Visual Studio Code"
    assert resolve_app("Show Files").name == "Files"
    assert resolve_app("Cursor kholo").name == "Cursor"


def test_resolves_installed_work_and_system_apps():
    expected = {
        "Open Calendar": "Calendar",
        "Open System Monitor": "System Monitor",
        "Open DBeaver": "DBeaver",
        "Open MongoDB Compass": "MongoDB Compass",
        "Open LibreOffice Writer": "LibreOffice Writer",
        "Open Cursor": "Cursor",
        "Open Antigravity": "Antigravity",
        "Open Thunderbird": "Thunderbird",
        "Open Remmina": "Remmina",
    }
    assert {command: resolve_app(command).name for command in expected} == expected


def test_requires_action_and_leaves_web_commands_to_site_router():
    assert resolve_app("I use Claude") is None
    assert resolve_app("Open Claude website") is None
    assert resolve_app("Open Claude in Brave") is None


def test_launch_uses_first_installed_executable_without_shell(monkeypatch):
    seen = []
    monkeypatch.setattr("vaani.apps.shutil.which", lambda name: "/usr/bin/gedit" if name == "gedit" else None)
    monkeypatch.setattr(
        "vaani.apps.subprocess.Popen",
        lambda args, **kwargs: seen.append((args, kwargs)) or SimpleNamespace(),
    )

    result = launch_app(AppTarget("Text Editor", ("gnome-text-editor", "gedit")))

    assert result == "Opened Text Editor."
    assert seen[0][0] == ["/usr/bin/gedit"]
    assert "shell" not in seen[0][1]


def test_missing_application_returns_visible_error(monkeypatch):
    monkeypatch.setattr("vaani.apps.shutil.which", lambda name: None)
    assert launch_app(AppTarget("Example", ("missing",))).startswith("Unable to open Example")
