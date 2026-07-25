from types import SimpleNamespace
from vaani.feedback import Feedback

def test_paplay_uses_safe_environment(monkeypatch):
    seen={}
    monkeypatch.setattr('vaani.feedback.Path.is_file', lambda self: True)
    def run(*a, **kw): seen.update(kw); return SimpleNamespace(returncode=0)
    assert Feedback(runner=run).play('success'); assert set(seen['env']) <= {'PATH','LANG','LC_ALL'}
def test_beep_fallback():
    called=[]; assert Feedback(beeper=lambda: called.append(1)).play('failure'); assert called
