from types import SimpleNamespace
from vaani.feedback import Feedback

def test_paplay_uses_safe_environment(monkeypatch):
    seen={}
    monkeypatch.setattr('vaani.platform.linux.feedback.Path.is_file', lambda self: True)
    def run(*a, **kw): seen.update(kw); return SimpleNamespace(returncode=0)
    assert Feedback(runner=run).play('success'); assert set(seen['env']) <= {'PATH','LANG','LC_ALL'}
def test_beep_fallback():
    called=[]; assert Feedback(beeper=lambda: called.append(1)).play('failure'); assert called

def test_processing_does_not_dismiss_indicator(tmp_path, monkeypatch):
    procs=[]
    def fake_popen(args, **_kw):
        procs.append(args)
        return SimpleNamespace(terminate=lambda: None, poll=lambda: None, pid=1)
    fb = Feedback(
        runner=lambda *_a, **_k: SimpleNamespace(returncode=0),
        beeper=lambda: None,
        popen=fake_popen,
        amplitude_path=tmp_path / "amp",
        control_path=tmp_path / "ctl.json",
        log_dir=tmp_path / "logs",
    )
    fb.play("start")
    assert fb.indicator is not None
    fb.play("processing")
    assert fb.indicator is not None
