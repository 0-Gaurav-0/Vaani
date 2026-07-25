from pathlib import Path
from types import SimpleNamespace
from vaani.controller import Controller
from vaani.types import AppState

class Rec:
    def start(self): return SimpleNamespace(path=Path('/tmp/a.wav'), duration_seconds=1)
    def stop(self): return SimpleNamespace(path=Path('/tmp/a.wav'), duration_seconds=1)
    def cleanup(self): pass

class Groq:
    def transcribe(self, *a, **k): return SimpleNamespace(text='hello', language='en')
    def cleanup(self, text, *a, **k): return SimpleNamespace(text=text.title(), used_fallback=False)
    def close(self): pass

class Delivery:
    def deliver(self, text): return 'paste_dispatched'
    def cancel(self): pass

class History:
    def __init__(self): self.rows=[]
    def insert(self, **kw): self.rows.append(kw); return 1
    def close(self): pass

def make():
    h=History(); c=Controller(recorder=Rec(), groq=Groq(), delivery=Delivery(), history=h,
                              key_provider=lambda:'key', max_duration=60)
    return c,h

def test_smart_and_literal_flow():
    c,h=make(); assert c.trigger('smart'); assert c.stop(); c._worker.join(1)
    assert h.rows and h.rows[0]['final_text']=='Hello'
    c.trigger('literal'); c.stop(); c._worker.join(1)
    assert h.rows[-1]['final_text']=='hello'

def test_cancel_returns_idle_without_history():
    c,h=make(); c.trigger('smart'); assert c.cancel(); assert c.state is AppState.IDLE; assert not h.rows

def test_busy_rejected():
    c,_=make(); assert c.trigger('smart'); assert not c.trigger('literal')

def test_assistant_uses_runner_displays_and_persists():
    class Runner:
        def run(self, prompt): return SimpleNamespace(stdout='answer', cancelled=False, timed_out=False)
    class Window:
        def __init__(self): self.results=[]
        def show(self, result): self.results.append(result.stdout)
    h=History(); w=Window()
    c=Controller(recorder=Rec(), groq=Groq(), delivery=Delivery(), history=h,
                 codex=Runner(), result_window=w, key_provider=lambda:'key')
    assert c.trigger_assistant(); assert c.stop(); c._worker.join(1)
    assert w.results == ['answer']
    assert h.rows[0]['mode'] == 'assistant' and h.rows[0]['final_text'] == 'answer'
    assert c.state is AppState.IDLE


def test_assistant_launches_resolved_desktop_app(monkeypatch):
    class Window:
        def __init__(self): self.results=[]
        def show_text(self, result): self.results.append(result)
    h=History(); w=Window()
    c=Controller(recorder=Rec(), groq=Groq(), delivery=Delivery(), history=h,
                 result_window=w, key_provider=lambda:'key')
    app = SimpleNamespace(name="Terminal")
    monkeypatch.setattr("vaani.controller.resolve_app", lambda text: app)
    monkeypatch.setattr("vaani.controller.launch_app", lambda target: "Opened Terminal.")

    assert c.trigger_assistant(); assert c.stop(); c._worker.join(1)

    assert w.results == ["Opened Terminal."]
    assert h.rows[0]["cleanup_status"] == "app_action"
    assert c.state is AppState.IDLE


def test_browser_intent_safe_action(monkeypatch):
    assert Controller._browser_intent("  Open   Chrome ")
    assert not Controller._browser_intent("open chrome and run ls")
    monkeypatch.setattr("vaani.controller.shutil.which", lambda name: None)
    assert "no supported browser" in Controller._open_browser()
    assert Controller._browser_intent("open brave browser")


def test_browser_defaults_to_brave(monkeypatch):
    seen = []
    monkeypatch.setattr("vaani.controller.shutil.which", lambda name: "/usr/bin/brave-browser" if "brave" in name else None)
    monkeypatch.setattr("vaani.controller.subprocess.Popen", lambda args, **kwargs: seen.append(args) or SimpleNamespace(poll=lambda: None))
    assert Controller._open_browser().startswith("Opened")
    assert "brave" in seen[0][0]
