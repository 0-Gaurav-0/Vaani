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


def test_indicator_control_file_stop(tmp_path):
    from vaani.indicator_protocol import write_command
    import struct
    import wave

    wav = tmp_path / "a.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        frames = b"".join(
            struct.pack("<h", 8000 if (i // 40) % 2 == 0 else -8000) for i in range(8000)
        )
        w.writeframes(frames)
    control = tmp_path / "indicator_control.json"
    amp = tmp_path / "amplitude"

    class RecFile:
        def start(self):
            return SimpleNamespace(path=wav, duration_seconds=1)

        def stop(self):
            return SimpleNamespace(path=wav, duration_seconds=1)

        def cleanup(self):
            pass

    h = History()
    c = Controller(
        recorder=RecFile(),
        groq=Groq(),
        delivery=Delivery(),
        history=h,
        key_provider=lambda: "key",
        amplitude_path=amp,
        indicator_control_path=control,
        max_duration=60,
    )
    assert c.trigger("smart")
    write_command(control, "stop")
    c._poll_indicator_control()
    if c._worker:
        c._worker.join(2)
    assert h.rows
    assert c.state is AppState.IDLE

def test_busy_rejected():
    c,_=make(); assert c.trigger('smart'); assert not c.trigger('literal')


def test_processing_blocks_new_input_without_dismiss_feedback():
    from vaani.types import AppState

    class FB:
        def __init__(self):
            self.cues = []

        def play(self, cue):
            self.cues.append(cue)
            return True

    h = History()
    fb = FB()
    c = Controller(
        recorder=Rec(),
        groq=Groq(),
        delivery=Delivery(),
        history=h,
        feedback=fb,
        key_provider=lambda: "key",
        max_duration=60,
    )
    c.state = AppState.PROCESSING
    before = list(fb.cues)
    assert not c.trigger("smart")
    assert not c.handle_hotkey("smart")
    # Must not play busy (that would dismiss the processing pill).
    assert fb.cues == before
    assert any(e.name == "busy" for e in c.events)

def test_assistant_uses_runner_displays_and_persists():
    class Runner:
        def run(self, prompt): return SimpleNamespace(stdout='answer', cancelled=False, timed_out=False)
    class Window:
        def __init__(self): self.results=[]
        def show(self, result): self.results.append(result.stdout)
    class CodingGroq(Groq):
        def transcribe(self, *a, **k): return SimpleNamespace(text='fix the flaky test', language='en')
    h=History(); w=Window()
    c=Controller(recorder=Rec(), groq=CodingGroq(), delivery=Delivery(), history=h,
                 codex=Runner(), result_window=w, key_provider=lambda:'key')
    assert c.trigger_assistant(); assert c.stop(); c._worker.join(1)
    assert w.results == ['answer']
    assert h.rows[0]['mode'] == 'assistant' and h.rows[0]['final_text'] == 'answer'
    assert c.state is AppState.IDLE


def test_assistant_qa_shows_answer_without_paste():
    class QAGroq(Groq):
        def transcribe(self, *a, **k):
            return SimpleNamespace(text='Who is the PM of India?', language='en')

        def answer(self, question, *a, **k):
            return SimpleNamespace(text='Narendra Modi', used_fallback=False)

    class FB:
        def __init__(self):
            self.cues = []
            self.answers = []

        def play(self, cue):
            self.cues.append(cue)
            return True

        def show_answer(self, question, answer):
            self.answers.append((question, answer))

    class DeliverySpy(Delivery):
        def __init__(self):
            self.calls = []

        def deliver(self, text, **_k):
            self.calls.append(text)
            return 'paste_dispatched'

    h = History()
    fb = FB()
    delivery = DeliverySpy()
    c = Controller(
        recorder=Rec(),
        groq=QAGroq(),
        delivery=delivery,
        history=h,
        feedback=fb,
        key_provider=lambda: 'key',
    )
    assert c.trigger_assistant()
    assert c.stop()
    c._worker.join(1)
    assert delivery.calls == []
    assert fb.answers == [('Who is the PM of India?', 'Narendra Modi')]
    assert h.rows[0]['cleanup_status'] == 'qa_answer'
    assert 'success' not in fb.cues


def test_assistant_ambiguous_pastes():
    class StatementGroq(Groq):
        def transcribe(self, *a, **k):
            return SimpleNamespace(text='please send this to John', language='en')

    class DeliverySpy(Delivery):
        def __init__(self):
            self.calls = []

        def deliver(self, text, **_k):
            self.calls.append(text)
            return 'paste_dispatched'

    h = History()
    delivery = DeliverySpy()
    c = Controller(
        recorder=Rec(),
        groq=StatementGroq(),
        delivery=delivery,
        history=h,
        key_provider=lambda: 'key',
    )
    assert c.trigger_assistant()
    assert c.stop()
    c._worker.join(1)
    assert delivery.calls == ['please send this to John']


def test_answer_prefix_skips_cleanup():
    class PrefixGroq(Groq):
        def transcribe(self, *a, **k):
            return SimpleNamespace(text='Answer this: capital of France', language='en')

        def answer(self, question, *a, **k):
            return SimpleNamespace(text='Paris', used_fallback=False)

        def cleanup(self, text, *a, **k):
            raise AssertionError('cleanup must not overwrite answers')

    h = History()
    c = Controller(
        recorder=Rec(),
        groq=PrefixGroq(),
        delivery=Delivery(),
        history=h,
        key_provider=lambda: 'key',
    )
    assert c.trigger('smart')
    assert c.stop()
    c._worker.join(1)
    assert h.rows[0]['final_text'] == 'Paris'
    assert h.rows[0]['mode'] == 'answer'


def test_delivery_failed_plays_failure_cue():
    class FailDelivery(Delivery):
        def deliver(self, text, **_k):
            return 'failed'

    class FB:
        def __init__(self):
            self.cues = []

        def play(self, cue):
            self.cues.append(cue)
            return True

    h = History()
    fb = FB()
    c = Controller(
        recorder=Rec(),
        groq=Groq(),
        delivery=FailDelivery(),
        history=h,
        feedback=fb,
        key_provider=lambda: 'key',
    )
    assert c.trigger('smart')
    assert c.stop()
    c._worker.join(1)
    assert fb.cues[-1] == 'failure'


def test_prompt_bleed_rejects_without_paste():
    class BleedGroq(Groq):
        def transcribe(self, *a, **k):
            from vaani.groq import TRANSCRIPTION_PROMPT
            return SimpleNamespace(text=TRANSCRIPTION_PROMPT, language='en')

    class DeliverySpy(Delivery):
        def __init__(self):
            self.calls = []

        def deliver(self, text, **_k):
            self.calls.append(text)
            return 'paste_dispatched'

    h = History()
    delivery = DeliverySpy()
    c = Controller(
        recorder=Rec(),
        groq=BleedGroq(),
        delivery=delivery,
        history=h,
        key_provider=lambda: 'key',
    )
    assert c.trigger('smart')
    assert c.stop()
    c._worker.join(1)
    assert delivery.calls == []
    assert not h.rows


def test_assistant_launches_resolved_desktop_app(monkeypatch):
    class Window:
        def __init__(self): self.results=[]
        def show_text(self, result): self.results.append(result)
    class OpenGroq(Groq):
        def transcribe(self, *a, **k):
            return SimpleNamespace(text='open Terminal', language='en')
    h=History(); w=Window()
    c=Controller(recorder=Rec(), groq=OpenGroq(), delivery=Delivery(), history=h,
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
    monkeypatch.setattr("vaani.platform.linux.browser.shutil.which", lambda name: None)
    monkeypatch.setattr(
        "vaani.platform.linux.browser.Path.exists",
        lambda self: False,
    )
    assert "no supported browser" in Controller._open_browser()
    assert Controller._browser_intent("open brave browser")


def test_browser_defaults_to_brave(monkeypatch):
    seen = []
    monkeypatch.setattr(
        "vaani.platform.linux.browser.shutil.which",
        lambda name: "/usr/bin/brave-browser" if "brave" in name else None,
    )
    monkeypatch.setattr(
        "vaani.platform.linux.browser.subprocess.Popen",
        lambda args, **kwargs: seen.append(args) or SimpleNamespace(poll=lambda: None),
    )
    assert Controller._open_browser().startswith("Opened")
    assert "brave" in seen[0][0]
