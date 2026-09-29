import json

import httpx
import pytest

from vaani.computer_use import Action, ComputerTask, Hooks, NimBrain, Snapshot, is_commit


class FakeHelper:
    def __init__(self, snaps):
        self.snaps = list(snaps)
        self.calls = []

    def snapshot(self):
        return self.snaps.pop(0) if len(self.snaps) > 1 else self.snaps[0]

    def call(self, cmd, **kw):
        self.calls.append((cmd, kw))
        if cmd == "screenshot":
            return {"ok": True, "png_b64": "AAAA", "scale": 0.5, "bbox": [100, 50, 800, 600]}
        return {"ok": True, "via": "action"}


class FakeBrain:
    def __init__(self, turns):
        self.turns = list(turns)
        self.seen = []

    def next_actions(self, messages):
        self.seen.append(messages)
        if not self.turns:
            raise RuntimeError("no more")
        return self.turns.pop(0)


class FakeX:
    def __init__(self):
        self.clicks, self.keys_pressed = [], []

    def click(self, x, y):
        self.clicks.append((x, y))

    def keys(self, chord):
        self.keys_pressed.append(chord)


def _hooks(confirm=True, cancelled=lambda: False):
    typed, confirms, statuses = [], [], []
    h = Hooks(
        status=statuses.append,
        confirm=lambda d: confirms.append(d) or confirm,
        cancelled=cancelled,
        type_text=lambda t: typed.append(t) or True,
        open_app=lambda n: "ok",
    )
    return h, typed, confirms, statuses


def _els(*names, editable=False):
    return [{"id": i, "role": "push button", "name": n, "bbox": [0, 0, 10, 10], "editable": editable}
            for i, n in enumerate(names)]


GEDIT = Snapshot("gedit", "Untitled Document 1", [0, 0, 800, 600], _els(*"abcdefghij"))


def test_keyboard_flow_in_editor_runs_without_confirmation():
    brain = FakeBrain([
        [Action("press_keys", {"keys": "ctrl+n"}), Action("type_text", {"text": "hello"}), Action("press_keys", {"keys": "Return"})],
        [Action("done", {"summary": "Wrote hello in a new file."})],
    ])
    hooks, typed, confirms, _ = _hooks()
    x = FakeX()
    out = ComputerTask("new file, write hello", hooks, brain=brain, helper=FakeHelper([GEDIT]), xinput=x).run()
    assert out == "Wrote hello in a new file."
    assert x.keys_pressed == ["ctrl+n", "Return"]  # Enter in an editor is a newline, not "send"
    assert typed == ["hello"] and confirms == []


def test_send_button_needs_explicit_confirm_and_cancel_stops_it():
    chat = Snapshot("Slack", "Rahul", [0, 0, 800, 600], _els("Message Rahul", "Send", *"abcdefgh"))
    brain = FakeBrain([[Action("type_text", {"text": "hi"}), Action("click", {"id": 1})]])
    hooks, typed, confirms, _ = _hooks(confirm=False)
    helper = FakeHelper([chat])
    out = ComputerTask("send hi to rahul", hooks, brain=brain, helper=helper, xinput=FakeX()).run()
    assert typed == ["hi"]
    assert confirms == ["Click “Send” in Slack"]
    assert ("click", {"id": 1}) not in helper.calls
    assert out.startswith("Cancelled before")


def test_enter_in_chat_app_is_a_commit():
    chat = Snapshot("Slack", "Rahul", [0, 0, 1, 1], [])
    assert is_commit(Action("press_keys", {"keys": "Return"}), chat)
    assert is_commit(Action("press_keys", {"keys": "ctrl+Return"}), GEDIT)
    assert is_commit(Action("press_keys", {"keys": "ctrl+w"}), GEDIT)
    assert not is_commit(Action("press_keys", {"keys": "ctrl+s"}), GEDIT)
    assert not is_commit(Action("type_text", {"text": "delete everything"}), chat)


def test_thin_tree_uses_window_screenshot_and_maps_coordinates():
    bare = Snapshot("claude-desktop", "Claude", [100, 50, 800, 600], [])
    brain = FakeBrain([
        [Action("click_xy", {"x": 200, "y": 100, "label": "Search box"})],
        [Action("done", {"summary": "ok"})],
    ])
    hooks, _typed, confirms, _ = _hooks(confirm=True)
    x = FakeX()
    task = ComputerTask("click search", hooks, brain=brain, helper=FakeHelper([bare]), xinput=x)
    task.run()
    # scale 0.5, window origin (100, 50) → screen (100 + 400, 50 + 200)
    assert x.clicks == [(500, 250)]
    assert task.used_screenshot
    img = brain.seen[0][1]["content"][1]
    assert img["type"] == "image_url"
    assert confirms  # coordinate clicks outside editors are gated


def test_model_failure_stops_without_retrying():
    brain = FakeBrain([[Action("press_keys", {"keys": "ctrl+n"})]])
    hooks, *_ = _hooks()
    x = FakeX()
    out = ComputerTask("x", hooks, brain=brain, helper=FakeHelper([GEDIT]), xinput=x).run()
    assert out.startswith("Stopped at step 2: model unavailable")
    assert x.keys_pressed == ["ctrl+n"]


def test_step_cap_and_cancel():
    brain = FakeBrain([[Action("wait", {"seconds": 0.2})]] * 5)
    hooks, *_ = _hooks()
    out = ComputerTask("x", hooks, brain=brain, helper=FakeHelper([GEDIT]), xinput=FakeX(), max_steps=2).run()
    assert out == "Stopped: reached 2 steps."
    hooks2, *_ = _hooks(cancelled=lambda: True)
    assert ComputerTask("x", hooks2, brain=FakeBrain([]), helper=FakeHelper([GEDIT]), xinput=FakeX()).run() == "Stopped."


def test_nim_brain_falls_back_to_next_model(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "k")
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body["model"])
        if body["model"] == "a":
            return httpx.Response(503, text="busy")
        return httpx.Response(200, json={"choices": [{"message": {"tool_calls": [
            {"function": {"name": "done", "arguments": json.dumps({"summary": "ok"})}}]}}]})

    brain = NimBrain(models=("a", "b"), transport=httpx.MockTransport(handler))
    acts = brain.next_actions([{"role": "user", "content": "x"}])
    assert seen == ["a", "b"] and acts[0].name == "done"


def test_never_screenshots_an_app_the_task_did_not_start_in_or_open():
    start = Snapshot("gedit", "doc", [0, 0, 800, 600], _els(*"abcdefghij"))
    other = Snapshot("claude-desktop", "Claude", [0, 0, 800, 600], [])  # thin tree → would want a screenshot
    brain = FakeBrain([[Action("press_keys", {"keys": "alt+tab"})], [Action("done", {"summary": "ok"})]])
    hooks, *_ = _hooks()
    helper = FakeHelper([start, other, other])
    task = ComputerTask("x", hooks, brain=brain, helper=helper, xinput=FakeX())
    task.run()
    assert not any(cmd == "screenshot" for cmd, _ in helper.calls)
    assert not task.used_screenshot


def test_controller_routes_jev_computer_intent(monkeypatch, tmp_path):
    from pathlib import Path
    from types import SimpleNamespace

    import vaani.computer_use as cu
    from vaani.controller import Controller
    from vaani.jev import JevDecision

    monkeypatch.setenv("VAANI_COMPUTER_USE", "1")
    monkeypatch.setenv("NVIDIA_API_KEY", "k")
    monkeypatch.setattr("vaani.controller.is_silent_wav", lambda p: False)
    monkeypatch.setattr("vaani.controller.load_context", lambda: "")
    ran = []

    class Task:
        def __init__(self, goal, hooks, **kw):
            self.goal, self.used_screenshot = goal, False

        def run(self):
            ran.append(self.goal)
            return "Sent hi to Rahul."

    monkeypatch.setattr(cu, "ComputerTask", Task)
    monkeypatch.setattr(cu, "A11yHelper", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(cu, "NimBrain", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(cu, "XInput", lambda: SimpleNamespace())
    answers, previews = [], []
    fb = SimpleNamespace(play=lambda c: True, show_answer=lambda q, a: answers.append(a),
                         show_confirm=lambda q, a, s: previews.append(a))
    rows = []
    rec = SimpleNamespace(start=lambda: SimpleNamespace(path=Path("/tmp/x.wav"), duration_seconds=1),
                          stop=lambda: SimpleNamespace(path=Path("/tmp/x.wav"), duration_seconds=1), cleanup=lambda: None)
    groq = SimpleNamespace(transcribe=lambda *a, **k: SimpleNamespace(text="slack pe rahul ko hi bhej do", language="en"))
    jev = SimpleNamespace(route=lambda *a, **k: JevDecision("computer", "In Slack, send hi to Rahul", "", 0.9))
    c = Controller(recorder=rec, groq=groq, delivery=None, history=SimpleNamespace(insert=lambda **kw: rows.append(kw)),
                   feedback=fb, key_provider=lambda: "k", jev=jev,
                   amplitude_path=tmp_path / "amp", indicator_control_path=tmp_path / "ctl.json")
    c.confirm_seconds = 0.1
    assert c.trigger_assistant() and c.stop()
    c._worker.join(3)
    assert previews == ["Do on screen: In Slack, send hi to Rahul"]
    assert ran == ["In Slack, send hi to Rahul"]
    assert answers[-1] == "Sent hi to Rahul." and rows[-1]["cleanup_status"] == "computer_task"
