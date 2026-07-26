from vaani.platform.macos.trust import python_paths, trust_help_text


def test_python_paths_include_executable(monkeypatch):
    monkeypatch.setattr("vaani.platform.macos.trust.sys.executable", "/tmp/fake-python")
    paths = python_paths()
    assert paths
    assert any("fake-python" in p or p.endswith("python") for p in paths)


def test_trust_help_mentions_accessibility():
    text = trust_help_text()
    assert "Accessibility" in text
    assert "Input Monitoring" in text
