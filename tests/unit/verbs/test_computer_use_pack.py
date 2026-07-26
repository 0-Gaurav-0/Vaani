"""Unit tests for computer-use / InputSynth rung-7 pack (T5.2)."""
from __future__ import annotations

from pathlib import Path

from vaani.exec.input import FakeInputSynth, TYPED_CAVEAT
from vaani.intent.grammar import match
from vaani.intent.schema import Context, FocusInfo, Intent, ProjectProfile, Status, Support
from vaani.platform.protocol import PlatformId
from vaani.verbs.packs.computer_use import (
    COMPUTER_USE_VERB_NAMES,
    build_computer_use_verbs,
    computer_use_patterns,
)
from vaani.verbs.packs.registry import PackRegistry
from vaani.verbs.registry import Registry


def _intent(
    verb: str,
    slots: dict | None = None,
    *,
    modifiers: frozenset[str] = frozenset(),
) -> Intent:
    return Intent(
        verb=verb,
        slots=slots or {},
        rung=7,
        confidence=1.0,
        source="test",
        mode="act",
        utterance=verb,
        raw_utterance=verb,
        modifiers=modifiers,
        brain=None,
    )


def _context(
    *,
    focus: FocusInfo | None = None,
    workspace: Path | None = None,
    platform: PlatformId = PlatformId.LINUX,
) -> Context:
    return Context(
        platform=platform,
        workspace=workspace,
        workspace_source="test",
        repo=None,
        project=None,
        focus=focus,
        screen=None,
        session=None,
    )


def _verbs(**kwargs):
    return {v.name: v for v in build_computer_use_verbs(**kwargs)}


def test_verb_names() -> None:
    synth = FakeInputSynth()
    assert set(_verbs(get_input=lambda: synth)) == COMPUTER_USE_VERB_NAMES


def test_patterns_cover_canonical_utterances() -> None:
    patterns = computer_use_patterns()
    assert match("refresh this tab", patterns)[0] == "browser.tab.reload"
    assert match("close this tab", patterns)[0] == "browser.tab.close"
    assert match("format this file", patterns)[0] == "editor.format"
    assert match("go to definition", patterns)[0] == "editor.nav"
    assert match("clear the terminal", patterns)[0] == "terminal.clear"
    assert match("run the last command again", patterns)[0] == "terminal.repeat"
    assert match("export the env from .env", patterns)[0] == "terminal.env.export"


def test_pack_disabled_unsupported(tmp_path: Path) -> None:
    packs = PackRegistry(tmp_path / "packs.json", which=lambda _b: None)
    assert packs.is_enabled("computer-use") is False
    registry = Registry()
    synth = FakeInputSynth()
    for verb in build_computer_use_verbs(get_input=lambda: synth):
        registry.register(verb)
    packs.apply(registry)
    support, note = registry.matrix()["browser.tab.reload"]["linux"]
    assert support is Support.UNSUPPORTED
    assert note == "pack disabled"
    assert "browser.tab.reload" not in {v.name for v in registry.enabled(PlatformId.LINUX)}


def test_rung7_results_include_caveat() -> None:
    synth = FakeInputSynth()
    verbs = _verbs(get_input=lambda: synth, get_platform=lambda: PlatformId.LINUX)
    focus = FocusInfo(app_id="gnome-terminal", window_title="shell")
    ctx = _context(focus=focus, workspace=Path("/tmp/proj"))
    for name in (
        "terminal.clear",
        "browser.tab.reload",
        "editor.format",
        "editor.nav",
        "terminal.cd",
    ):
        # Use matching focus per verb family.
        if name.startswith("browser"):
            focus = FocusInfo(app_id="firefox", window_title="Mozilla Firefox")
        elif name.startswith("editor"):
            focus = FocusInfo(app_id="code", window_title="main.py — project")
        else:
            focus = FocusInfo(app_id="gnome-terminal", window_title="shell")
        ctx = _context(focus=focus, workspace=Path("/tmp/proj"))
        result = verbs[name].handler(_intent(name), ctx)
        assert result.status is Status.OK, name
        assert TYPED_CAVEAT in result.detail, name
        assert result.rung == 7


def test_focus_precondition_aborts_before_keys() -> None:
    synth = FakeInputSynth()
    verbs = _verbs(get_input=lambda: synth)
    # Editor focused while asking for terminal.clear.
    ctx = _context(focus=FocusInfo(app_id="code", window_title="file.py"))
    result = verbs["terminal.clear"].handler(_intent("terminal.clear"), ctx)
    assert result.status is Status.FAILED
    assert "Focus precondition" in result.summary or "aborted" in result.detail
    assert synth.calls == []


def test_browser_reload_sends_hotkey() -> None:
    synth = FakeInputSynth()
    verbs = _verbs(get_input=lambda: synth, get_platform=lambda: PlatformId.LINUX)
    ctx = _context(focus=FocusInfo(app_id="Google Chrome", window_title="Tab"))
    result = verbs["browser.tab.reload"].handler(_intent("browser.tab.reload"), ctx)
    assert result.status is Status.OK
    assert ("hotkey", ("ctrl", "r")) in synth.calls
    assert TYPED_CAVEAT in result.detail


def test_terminal_repeat_refused() -> None:
    synth = FakeInputSynth()
    verbs = _verbs(get_input=lambda: synth)
    ctx = _context(focus=FocusInfo(app_id="terminal", window_title="zsh"))
    result = verbs["terminal.repeat"].handler(_intent("terminal.repeat"), ctx)
    assert result.status is Status.REFUSED
    assert synth.calls == []
    assert "TERM-SESS-04" in result.detail or "cannot know" in result.detail.casefold()


def test_terminal_env_export_r4_refused_redacted() -> None:
    synth = FakeInputSynth()
    verbs = _verbs(get_input=lambda: synth)
    ctx = _context(
        focus=FocusInfo(app_id="terminal", window_title="zsh"),
        workspace=Path("/tmp/proj"),
    )
    result = verbs["terminal.env.export"].handler(
        _intent("terminal.env.export"), ctx
    )
    assert result.status is Status.REFUSED
    assert synth.calls == []
    assert "<redacted>" in result.evidence
    assert "SECRET" not in result.detail
    assert "password" not in result.detail.casefold()


def test_computer_use_off_by_default_in_caps(tmp_path: Path) -> None:
    packs = PackRegistry(tmp_path / "packs.json", which=lambda _b: None)
    payload = packs.caps_payload()
    assert payload["computer-use"]["enabled"] is False
    assert payload["computer-use"]["support"] == "unsupported"
    assert payload["computer-use"]["note"] == "pack disabled"
