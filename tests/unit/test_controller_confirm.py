"""Controller confirm wiring (T2.1 + parallel-agents §0)."""
from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

from vaani.controller import Controller
from vaani.indicator_protocol import (
    read_pending_id,
    read_phase,
    write_command,
    write_pending_id,
)
from vaani.intent.schema import RiskClass, Status
from vaani.types import AppState


class Rec:
    def start(self):
        return SimpleNamespace(path=Path("/tmp/a.wav"), duration_seconds=1)

    def stop(self):
        return SimpleNamespace(path=Path("/tmp/a.wav"), duration_seconds=1)

    def cleanup(self):
        pass


class Groq:
    def __init__(self, text: str = "quit Slack"):
        self.text = text

    def transcribe(self, *a, **k):
        return SimpleNamespace(text=self.text, language="en")

    def cleanup(self, text, *a, **k):
        return SimpleNamespace(text=text, used_fallback=False)

    def close(self):
        pass


class Delivery:
    def deliver(self, text, snapshot=None):
        return "paste_dispatched"

    def cancel(self):
        pass


class History:
    def __init__(self):
        self.rows = []

    def insert(self, **kw):
        self.rows.append(kw)
        return 1

    def close(self):
        pass


class Window:
    def __init__(self):
        self.results = []

    def show_text(self, text):
        self.results.append(text)


def _controller(tmp_path: Path, *, text: str = "quit Slack", system=None):
    control = tmp_path / "indicator_control.json"
    amp = tmp_path / "amplitude"
    h = History()
    w = Window()
    c = Controller(
        recorder=Rec(),
        groq=Groq(text),
        delivery=Delivery(),
        history=h,
        result_window=w,
        key_provider=lambda: "key",
        amplitude_path=amp,
        indicator_control_path=control,
        system=system,
        max_duration=60,
    )
    return c, h, w, control


def test_r2_verb_stages_pending_instead_of_executing(tmp_path: Path, monkeypatch):
    calls: list[str] = []

    def fake_quit(name, platform):
        calls.append(name)
        from vaani.intent.schema import Result

        return Result(status=Status.OK, summary=f"Quit {name}.", rung=1)

    c, h, w, control = _controller(tmp_path)
    # Patch the registered handler's resolve path via registry rebuild is heavy;
    # monkeypatch the core quit handler used by the verb.
    monkeypatch.setattr(
        "vaani.verbs.packs.core.quit_app",
        fake_quit,
        raising=False,
    )
    # Ensure app.quit handler sees our spy through a fresh stage path:
    verb = c.registry.get("app.quit")
    assert verb is not None and verb.risk is RiskClass.R2
    original = verb.handler

    def spy(intent, context):
        calls.append(str(intent.slots.get("name")))
        return original(intent, context)

    object.__setattr__(verb, "handler", spy)

    assert c.trigger_assistant()
    assert c.stop()
    c._worker.join(2)

    assert c.state is AppState.IDLE
    pending = c.confirm.peek()
    assert pending is not None
    assert pending.verb == "app.quit"
    assert calls == []  # handler not run yet
    assert not h.rows
    assert read_phase(c.indicator_phase_path) == "confirming"
    assert read_pending_id(c.indicator_pending_path) == pending.id
    assert w.results  # materialized argv / confirm text shown


def test_pill_approve_and_reject_commands(tmp_path: Path):
    c, h, _w, control = _controller(tmp_path, text="quit Slack")
    verb = c.registry.get("app.quit")
    assert verb is not None
    ran = {"n": 0}

    def handler(intent, context):
        ran["n"] += 1
        from vaani.intent.schema import Result

        return Result(status=Status.OK, summary="Quit Slack.", rung=1)

    object.__setattr__(verb, "handler", handler)

    assert c.trigger_assistant()
    assert c.stop()
    c._worker.join(2)
    pending = c.confirm.peek()
    assert pending is not None

    write_command(control, f"approve:{pending.id}")
    c._poll_indicator_control()
    assert ran["n"] == 1
    assert c.confirm.peek() is None
    assert h.rows and h.rows[0]["final_text"] == "Quit Slack."

    # Fresh pending then reject
    assert c.trigger_assistant()
    assert c.stop()
    c._worker.join(2)
    pending2 = c.confirm.peek()
    assert pending2 is not None
    write_command(control, f"reject:{pending2.id}")
    c._poll_indicator_control()
    assert c.confirm.peek() is None
    assert ran["n"] == 1  # no second execute


def test_enter_approve_esc_reject_methods(tmp_path: Path):
    c, _h, _w, _control = _controller(tmp_path, text="quit Slack")
    verb = c.registry.get("app.quit")
    assert verb is not None
    ran = {"n": 0}

    def handler(intent, context):
        ran["n"] += 1
        from vaani.intent.schema import Result

        return Result(status=Status.OK, summary="Quit Slack.", rung=1)

    object.__setattr__(verb, "handler", handler)

    assert c.trigger_assistant()
    assert c.stop()
    c._worker.join(2)
    assert c.approve_pending(via="hotkey")
    assert ran["n"] == 1

    assert c.trigger_assistant()
    assert c.stop()
    c._worker.join(2)
    assert c.cancel()  # Esc family while pending
    assert c.confirm.peek() is None
    assert ran["n"] == 1


def test_new_utterance_invalidates_pending(tmp_path: Path):
    c, _h, _w, _control = _controller(tmp_path, text="quit Slack")
    verb = c.registry.get("app.quit")
    assert verb is not None
    object.__setattr__(
        verb,
        "handler",
        lambda intent, context: (__import__("vaani.intent.schema", fromlist=["Result"]).Result(
            status=Status.OK, summary="x", rung=1
        )),
    )
    assert c.trigger_assistant()
    assert c.stop()
    c._worker.join(2)
    first = c.confirm.peek()
    assert first is not None

    # New dictation cancels pending (does not approve).
    assert c.trigger("smart")
    assert c.confirm.peek() is None
    assert any(e.name == "confirm_invalidated" for e in c.events)
    c.cancel()


def test_double_approve_control_command(tmp_path: Path):
    c, _h, _w, control = _controller(tmp_path, text="quit Slack")
    verb = c.registry.get("app.quit")
    assert verb is not None
    ran = {"n": 0}

    def handler(intent, context):
        ran["n"] += 1
        from vaani.intent.schema import Result

        return Result(status=Status.OK, summary="ok", rung=1)

    object.__setattr__(verb, "handler", handler)
    assert c.trigger_assistant()
    assert c.stop()
    c._worker.join(2)
    pending = c.confirm.peek()
    assert pending is not None
    write_command(control, f"approve:{pending.id}")
    c._poll_indicator_control()
    write_command(control, f"approve:{pending.id}")
    c._poll_indicator_control()
    assert ran["n"] == 1


def test_ttl_expire_clears_confirming(tmp_path: Path):
    c, _h, _w, _control = _controller(tmp_path, text="quit Slack")
    # Short TTL for the test (ConfirmEngine clamps constructor ttl to >=0.1).
    c.confirm._ttl = 0.1
    verb = c.registry.get("app.quit")
    assert verb is not None
    object.__setattr__(
        verb,
        "handler",
        lambda i, ctx: (__import__("vaani.intent.schema", fromlist=["Result"]).Result(
            status=Status.OK, summary="x", rung=1
        )),
    )
    assert c.trigger_assistant()
    assert c.stop()
    c._worker.join(2)
    assert c.confirm.peek() is not None
    # Wait past TTL, then expire explicitly. peek() soft-hides expired
    # actions without emitting, so do not rely on a poll loop alone.
    time.sleep(0.15)
    c._expire_confirm()
    assert c.confirm.peek() is None
    assert any(e.name == "confirm_expired" for e in c.events)


def test_agent_via_blocked_on_r3(tmp_path: Path):
    from vaani.intent.schema import Context, Intent, Result, SlotSpec, Support, Verb
    from vaani.platform.protocol import PlatformId

    c, _h, _w, _control = _controller(tmp_path)
    calls: list[str] = []

    def handler(intent, context):
        calls.append("ran")
        return Result(status=Status.OK, summary="done", rung=1)

    dangerous = Verb(
        name="vcs.push.force",
        title="Force push",
        slots={},
        rung=4,
        risk=RiskClass.R3,
        requires=frozenset(),
        support={
            PlatformId.LINUX: Support.SUPPORTED,
            PlatformId.MACOS: Support.SUPPORTED,
            PlatformId.WINDOWS: Support.SUPPORTED,
        },
        undo=None,
        pack="test",
        handler=handler,
    )
    c.registry.register(dangerous)
    intent = Intent(
        verb="vcs.push.force",
        slots={},
        rung=4,
        confidence=1.0,
        source="test",
        mode="act",
        utterance="force push",
        raw_utterance="force push",
        modifiers=frozenset(),
        brain="codex",
    )
    context = Context(
        platform=PlatformId.LINUX,
        workspace=None,
        workspace_source="home",
        repo=None,
        project=None,
        focus=None,
        screen=None,
        session=None,
    )
    pending = c.confirm.stage(intent, dangerous, ("git", "push", "--force-with-lease"), context=context)
    write_pending_id(c.indicator_pending_path, pending.id)
    assert c.confirm.approve(pending.id, via="agent") is None
    assert calls == []
    assert c.approve_pending(pending.id, via="pill")
    assert calls == ["ran"]


def test_proc_kill_stages_disambiguation_not_confirm(tmp_path: Path):
    from vaani.platform.protocol import ProcInfo

    class Sys:
        def list_named(self, name: str):
            return (
                ProcInfo(pid=11, name="node"),
                ProcInfo(pid=12, name="node"),
            )

        def kill_pids(self, pids, *, signal="term"):
            raise AssertionError("must not kill during disambiguation")

    c, _h, w, _control = _controller(
        tmp_path, text="kill the process named node", system=Sys()
    )
    assert c.trigger_assistant()
    assert c.stop()
    c._worker.join(2)
    assert c.confirm.peek() is None
    prompt = c.disambiguate.peek()
    assert prompt is not None
    assert len(prompt.options) == 2
    assert read_phase(c.indicator_phase_path) == "confirming"
    assert w.results
    # Approve must not default-pick.
    assert c.approve_pending(via="hotkey") is False
    assert c.disambiguate.peek() is not None


def test_disambiguation_timeout_cancels_without_selecting(tmp_path: Path):
    from vaani.intent.schema import DisambiguationOption, DisambiguationPrompt
    from vaani.platform.protocol import PlatformId
    from vaani.intent.schema import Context, Intent, SlotSpec, Support, Verb

    c, _h, _w, _control = _controller(tmp_path)
    c.disambiguate._ttl = 0.1

    def handler(intent, context):
        raise AssertionError("handler must not run on timeout")

    verb = Verb(
        name="system.proc.kill",
        title="Kill",
        slots={"name": SlotSpec(type="str")},
        rung=2,
        risk=RiskClass.R2,
        requires=frozenset(),
        support={
            PlatformId.LINUX: Support.SUPPORTED,
            PlatformId.MACOS: Support.SUPPORTED,
            PlatformId.WINDOWS: Support.SUPPORTED,
        },
        undo=None,
        pack="procs",
        handler=handler,
    )
    intent = Intent(
        verb="system.proc.kill",
        slots={"name": "node"},
        rung=2,
        confidence=1.0,
        source="test",
        mode="act",
        utterance="kill node",
        raw_utterance="kill node",
        modifiers=frozenset(),
        brain=None,
    )
    context = Context(
        platform=PlatformId.LINUX,
        workspace=None,
        workspace_source="home",
        repo=None,
        project=None,
        focus=None,
        screen=None,
        session=None,
    )
    prompt = DisambiguationPrompt(
        id="dx1",
        question="Which node?",
        options=(
            DisambiguationOption(key="11", label="node 11", payload={"pid": 11}),
            DisambiguationOption(key="12", label="node 12", payload={"pid": 12}),
        ),
        verb="system.proc.kill",
        slots={"name": "node"},
        expires_at=0.0,
    )
    c.disambiguate.stage(intent, verb, prompt, context=context)
    time.sleep(0.15)
    c._expire_disambiguate()
    assert c.disambiguate.peek() is None
    assert c.disambiguate.claim_selection() is None
    assert any(e.name == "disambiguate_expired" for e in c.events)


def test_disambiguation_voice_ordinal_then_confirm(tmp_path: Path):
    from vaani.platform.protocol import ProcInfo

    killed: list[tuple] = []

    class Sys:
        def list_named(self, name: str):
            all_procs = (
                ProcInfo(pid=11, name="node"),
                ProcInfo(pid=12, name="node"),
            )
            # After ordinal selection, slots include pid.
            return all_procs

        def kill_pids(self, pids, *, signal="term"):
            killed.append((tuple(pids), signal))
            from vaani.intent.schema import Result

            return Result(status=Status.OK, summary="Signaled", rung=2)

    c, _h, _w, control = _controller(
        tmp_path, text="kill the process named node", system=Sys()
    )
    assert c.trigger_assistant()
    assert c.stop()
    c._worker.join(2)
    prompt = c.disambiguate.peek()
    assert prompt is not None

    # Voice ordinal via a follow-up assistant utterance.
    c.groq.text = "the first one"
    assert c.trigger_assistant()
    assert c.stop()
    c._worker.join(2)
    assert c.disambiguate.peek() is None
    pending = c.confirm.peek()
    assert pending is not None
    assert pending.slots.get("pid") == 11

    write_command(control, f"approve:{pending.id}")
    c._poll_indicator_control()
    assert killed == [((11,), "term")]
