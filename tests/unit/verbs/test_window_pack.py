"""T5.1 window pack verbs + patterns (fakes only)."""
from __future__ import annotations

from vaani.apps import launch_app, resolve_app
from vaani.intent.grammar import match
from vaani.intent.schema import Context, Intent, Result, Status, Support
from vaani.platform.protocol import PlatformId
from vaani.policy.dryrun import materialize_argv
from vaani.sites import resolve_site
from vaani.verbs.packs.core import build_core_registry
from vaani.verbs.packs.window import WINDOW_VERB_NAMES, window_patterns


class _FakeWindow:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.focus_result = Result(status=Status.OK, summary="Focused Chrome", rung=4)
        self.tile_result = Result(status=Status.OK, summary="Tiled window left", rung=4)
        self.hide_result = Result(status=Status.OK, summary="Hid other windows", rung=4)

    def focus(self, target: str) -> Result:
        self.calls.append(("focus", target))
        return self.focus_result

    def tile(self, side: str) -> Result:
        self.calls.append(("tile", side))
        return self.tile_result

    def hide_others(self) -> Result:
        self.calls.append(("hide_others", None))
        return self.hide_result


def _intent(verb: str, slots: dict | None = None, utterance: str = "") -> Intent:
    return Intent(
        verb=verb,
        slots=slots or {},
        rung=4,
        confidence=1.0,
        source="test",
        mode="act",
        utterance=utterance or verb,
        raw_utterance=utterance or verb,
        modifiers=frozenset(),
        brain=None,
    )


def _context(platform: PlatformId = PlatformId.LINUX) -> Context:
    return Context(
        platform=platform,
        workspace=None,
        workspace_source="test",
        repo=None,
        project=None,
        focus=None,
        screen=None,
        session=None,
    )


def _registry(**kwargs):
    kwargs.setdefault("resolve_app_fn", resolve_app)
    kwargs.setdefault("launch_app_fn", launch_app)
    kwargs.setdefault("resolve_site_fn", resolve_site)
    kwargs.setdefault("open_browser_fn", lambda **_k: "Opened browser.")
    return build_core_registry(**kwargs)


def test_window_pack_registers_verbs() -> None:
    registry, _ = _registry()
    assert WINDOW_VERB_NAMES <= set(registry.matrix())


def test_window_patterns_ui_win_phrases() -> None:
    patterns = window_patterns()
    focus = match("focus Chrome", patterns)
    assert focus is not None and focus[0] == "window.focus"
    assert focus[1]["target"].casefold() == "chrome"

    tile = match("split the window left", patterns)
    assert tile is not None and tile[0] == "window.tile"
    assert tile[1]["side"] == "left"

    hide = match("show desktop", patterns)
    assert hide is not None and hide[0] == "window.hide_others"


def test_handlers_forward_to_window_control() -> None:
    window = _FakeWindow()
    registry, _ = _registry(get_window=lambda: window)

    focus = registry.get("window.focus")
    assert focus is not None
    assert (
        focus.handler(_intent("window.focus", {"target": "Chrome"}), _context()).status
        is Status.OK
    )

    tile = registry.get("window.tile")
    assert tile is not None
    assert (
        tile.handler(_intent("window.tile", {"side": "left"}), _context()).status
        is Status.OK
    )

    hide = registry.get("window.hide_others")
    assert hide is not None
    assert hide.handler(_intent("window.hide_others"), _context()).status is Status.OK
    assert [c[0] for c in window.calls] == ["focus", "tile", "hide_others"]


def test_missing_window_control_is_unsupported() -> None:
    registry, _ = _registry(get_window=lambda: None)
    verb = registry.get("window.focus")
    assert verb is not None
    result = verb.handler(_intent("window.focus", {"target": "X"}), _context())
    assert result.status is Status.UNSUPPORTED
    assert "WindowControl" in (result.detail or "")


def test_focus_support_windows_degraded() -> None:
    registry, _ = _registry()
    support, note = registry.matrix()["window.focus"]["windows"]
    assert support is Support.DEGRADED
    assert note.strip()


def test_dryrun_materialize_window_argv() -> None:
    assert materialize_argv(
        "window.focus",
        {"target": "Chrome"},
        platform=PlatformId.LINUX,
    ) == ("wmctrl", "-a", "Chrome")
    assert materialize_argv(
        "window.tile",
        {"side": "left"},
        platform=PlatformId.WINDOWS,
    ) == ("MoveWindow", "left")
    mac = materialize_argv(
        "window.hide_others",
        {},
        platform=PlatformId.MACOS,
    )
    assert mac is not None
    assert mac[0] == "osascript"
