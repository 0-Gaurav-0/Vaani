from vaani.platform.windows.trust import python_paths, setup_help_text


def test_python_paths_include_executable(monkeypatch):
    monkeypatch.setattr("vaani.platform.windows.trust.sys.executable", r"C:\fake\python.exe")
    paths = python_paths()
    assert paths
    assert any("python" in p.lower() for p in paths)


def test_setup_help_mentions_hold_to_talk_and_mic():
    text = setup_help_text()
    assert "Ctrl+Space" in text
    assert "Microphone" in text or "microphone" in text
    assert "hold" in text.lower() or "Hold" in text


def test_setup_help_brief():
    text = setup_help_text(brief=True)
    assert "Ctrl+Space" in text
    assert "windows.md" in text
