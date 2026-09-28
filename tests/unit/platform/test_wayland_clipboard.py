import subprocess
import pytest
from vaani.platform.linux.wayland.clipboard import WlClipboard, WtypePaster, WtypeUnavailable

class FakeCompleted:
    def __init__(self, returncode=0, stdout=""): self.returncode, self.stdout = returncode, stdout

def test_set_text_calls_wl_copy(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append((a, k)) or FakeCompleted())
    WlClipboard().set_text("hello")
    assert calls[0][0][0] == ["wl-copy"]
    assert calls[0][1]["input"] == "hello"

def test_read_text_returns_none_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeCompleted(returncode=1, stdout=""))
    assert WlClipboard().read_text() is None

def test_read_text_returns_stdout_on_success(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: FakeCompleted(returncode=0, stdout="hi"))
    assert WlClipboard().read_text() == "hi"

def test_paste_raises_when_wtype_missing(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    with pytest.raises(WtypeUnavailable):
        WtypePaster().paste()

def test_paste_sends_ctrl_v_when_available(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/wtype")
    calls = []
    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return FakeCompleted()
    monkeypatch.setattr(subprocess, "run", fake_run)
    WtypePaster().paste()
    assert calls[0] == ["wtype", "-M", "ctrl", "-k", "v", "-m", "ctrl"]

def test_paste_raises_on_compositor_rejection(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/wtype")
    def fake_run(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd)
    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(WtypeUnavailable):
        WtypePaster().paste()
