"""Unit tests for T1.3 core verb pack handlers (fakes only)."""
from __future__ import annotations

from pathlib import Path

from vaani.apps import launch_app, resolve_app
from vaani.intent.schema import Context, Intent, Result, Status, Support
from vaani.platform.protocol import PlatformId
from vaani.sites import resolve_site
from vaani.verbs.packs.core import CORE_VERB_NAMES, build_core_registry
from vaani.verbs.packs.procs import PROCS_VERB_NAMES


class _FakeSystem:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.ip = "192.168.1.10"
        self.volume_result = Result(status=Status.OK, summary="Volume set to 30%", rung=2)
        self.mute_result = Result(status=Status.OK, summary="Muted", rung=2)
        self.dnd_result = Result(
            status=Status.UNSUPPORTED,
            summary="Do Not Disturb is not available",
            detail="no API",
            rung=1,
        )
        self.lock_result = Result(status=Status.OK, summary="Screen locked", rung=1)
        self.sleep_result = Result(status=Status.OK, summary="Display sleeping", rung=1)

    def volume_set(self, pct: int) -> Result:
        self.calls.append(("volume_set", pct))
        return self.volume_result

    def mute(self, enabled: bool) -> Result:
        self.calls.append(("mute", enabled))
        return self.mute_result

    def dnd(self, enabled: bool) -> Result:
        self.calls.append(("dnd", enabled))
        return self.dnd_result

    def lock(self) -> Result:
        self.calls.append(("lock", None))
        return self.lock_result

    def display_sleep(self) -> Result:
        self.calls.append(("display_sleep", None))
        return self.sleep_result

    def local_ip(self) -> str:
        self.calls.append(("local_ip", None))
        return self.ip


class _FakeDelivery:
    def __init__(self, status: str = "clipboard_only") -> None:
        self.status = status
        self.texts: list[str] = []

    def deliver(self, text: str, snapshot=None) -> str:
        _ = snapshot
        self.texts.append(text)
        return self.status


def _intent(verb: str, slots: dict | None = None, utterance: str = "") -> Intent:
    return Intent(
        verb=verb,
        slots=slots or {},
        rung=1,
        confidence=1.0,
        source="test",
        mode="act",
        utterance=utterance or verb,
        raw_utterance=utterance or verb,
        modifiers=frozenset(),
        brain=None,
    )


def _context(
    platform: PlatformId = PlatformId.MACOS,
    workspace: Path | None = None,
) -> Context:
    return Context(
        platform=platform,
        workspace=workspace,
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


def test_core_pack_registers_all_t13_verbs() -> None:
    registry, _ = _registry()
    names = set(registry.matrix())
    assert CORE_VERB_NAMES <= names
    assert PROCS_VERB_NAMES <= names
    assert names == CORE_VERB_NAMES | PROCS_VERB_NAMES


def test_caps_matrix_three_platforms_for_new_verbs() -> None:
    registry, _ = _registry()
    matrix = registry.matrix()
    for name in (
        "app.quit",
        "system.volume.set",
        "system.dnd.set",
        "system.lock",
        "system.display.sleep",
        "system.ip.copy",
        "files.reveal",
        "files.open_dir",
        "site.search",
        "browser.window.private",
    ):
        row = matrix[name]
        assert set(row) == {"linux", "macos", "windows"}
        for support, _note in row.values():
            assert support in {Support.SUPPORTED, Support.DEGRADED, Support.UNSUPPORTED}


def test_volume_and_mute_call_system() -> None:
    system = _FakeSystem()
    registry, _ = _registry(get_system=lambda: system)
    vol = registry.get("system.volume.set")
    assert vol is not None
    result = vol.handler(_intent("system.volume.set", {"level": 30}), _context())
    assert result.status is Status.OK
    assert system.calls == [("volume_set", 30)]

    mute = vol.handler(_intent("system.volume.set", {"muted": True}), _context())
    assert mute.status is Status.OK
    assert system.calls[-1] == ("mute", True)


def test_volume_without_system_is_unsupported() -> None:
    registry, _ = _registry(get_system=lambda: None)
    verb = registry.get("system.volume.set")
    assert verb is not None
    result = verb.handler(_intent("system.volume.set", {"level": 10}), _context())
    assert result.status is Status.UNSUPPORTED
    assert "SystemControl" in (result.detail or "")


def test_dnd_lock_sleep_forward_system_results() -> None:
    system = _FakeSystem()
    registry, _ = _registry(get_system=lambda: system)

    dnd = registry.get("system.dnd.set")
    assert dnd is not None
    dnd_result = dnd.handler(_intent("system.dnd.set", {"enabled": True}), _context())
    assert dnd_result.status is Status.UNSUPPORTED  # honest degraded path

    lock = registry.get("system.lock")
    assert lock is not None
    assert lock.handler(_intent("system.lock"), _context()).status is Status.OK

    sleep = registry.get("system.display.sleep")
    assert sleep is not None
    assert sleep.handler(_intent("system.display.sleep"), _context()).status is Status.OK
    assert [c[0] for c in system.calls] == ["dnd", "lock", "display_sleep"]


def test_ip_copy_uses_delivery() -> None:
    system = _FakeSystem()
    delivery = _FakeDelivery()
    registry, _ = _registry(
        get_system=lambda: system,
        get_delivery=lambda: delivery,
    )
    verb = registry.get("system.ip.copy")
    assert verb is not None
    result = verb.handler(_intent("system.ip.copy"), _context())
    assert result.status is Status.OK
    assert result.detail == "192.168.1.10"
    assert delivery.texts == ["192.168.1.10"]


def test_ip_copy_without_delivery_unsupported() -> None:
    system = _FakeSystem()
    registry, _ = _registry(
        get_system=lambda: system,
        get_delivery=lambda: None,
    )
    verb = registry.get("system.ip.copy")
    assert verb is not None
    result = verb.handler(_intent("system.ip.copy"), _context())
    assert result.status is Status.UNSUPPORTED


def test_app_quit_uses_injected_fn() -> None:
    seen: list[tuple[str, PlatformId]] = []

    def quit_app(name: str, platform: PlatformId) -> Result:
        seen.append((name, platform))
        return Result(status=Status.OK, summary=f"Quit {name}", rung=1)

    registry, _ = _registry(quit_app_fn=quit_app)
    verb = registry.get("app.quit")
    assert verb is not None
    result = verb.handler(
        _intent("app.quit", {"name": "Slack"}),
        _context(PlatformId.LINUX),
    )
    assert result.status is Status.OK
    assert seen == [("Slack", PlatformId.LINUX)]


def test_files_reveal_requires_workspace() -> None:
    registry, _ = _registry()
    verb = registry.get("files.reveal")
    assert verb is not None
    missing = verb.handler(_intent("files.reveal"), _context(workspace=None))
    assert missing.status is Status.FAILED

    revealed: list[Path] = []

    def reveal(path: Path, platform: PlatformId) -> Result:
        revealed.append(path)
        _ = platform
        return Result(status=Status.OK, summary="Revealed", detail=str(path), rung=3)

    registry, _ = _registry(reveal_fn=reveal)
    verb = registry.get("files.reveal")
    assert verb is not None
    path = Path("/tmp/vaani-project")
    ok = verb.handler(_intent("files.reveal"), _context(workspace=path))
    assert ok.status is Status.OK
    assert revealed == [path]


def test_files_open_dir_uses_known_folder_resolver() -> None:
    seen: list[tuple[str, PlatformId]] = []

    def resolve(name: str, platform: PlatformId, **_kwargs) -> Path | None:
        seen.append((name, platform))
        return Path("/resolved/Downloads")

    opened: list[Path] = []

    def open_dir(path: Path, platform: PlatformId) -> Result:
        opened.append(path)
        _ = platform
        return Result(status=Status.OK, summary="Opened Downloads", detail=str(path), rung=1)

    registry, _ = _registry(resolve_folder_fn=resolve, open_dir_fn=open_dir)
    verb = registry.get("files.open_dir")
    assert verb is not None
    result = verb.handler(
        _intent("files.open_dir", {"folder": "Downloads"}),
        _context(PlatformId.LINUX),
    )
    assert result.status is Status.OK
    assert seen == [("Downloads", PlatformId.LINUX)]
    assert opened == [Path("/resolved/Downloads")]


def test_site_search_and_private_window() -> None:
    urls: list[str] = []

    def open_browser(*, prefer_brave: bool = True, url: str = "about:blank") -> str:
        _ = prefer_brave
        urls.append(url)
        return "Opened browser."

    private_calls: list[str] = []

    def open_private(*, prefer: str, platform: PlatformId) -> str:
        private_calls.append(prefer)
        _ = platform
        return "Opened private browser window."

    registry, _ = _registry(
        open_browser_fn=open_browser,
        open_private_fn=open_private,
    )
    search = registry.get("site.search")
    assert search is not None
    result = search.handler(
        _intent("site.search", {"query": "pulseaudio mute microphone"}),
        _context(),
    )
    assert result.status is Status.OK
    assert "google.com/search" in urls[0]
    assert "pulseaudio" in urls[0]

    private = registry.get("browser.window.private")
    assert private is not None
    assert (
        private.handler(_intent("browser.window.private"), _context()).status
        is Status.OK
    )
    assert private_calls == ["brave"]


def test_localhost_site_open_builds_url() -> None:
    urls: list[str] = []

    def open_browser(*, prefer_brave: bool = True, url: str = "about:blank") -> str:
        _ = prefer_brave
        urls.append(url)
        return "Opened browser."

    registry, _ = _registry(open_browser_fn=open_browser)
    verb = registry.get("site.open")
    assert verb is not None
    result = verb.handler(
        _intent("site.open", {"port": "3000", "name": "localhost"}),
        _context(),
    )
    assert result.status is Status.OK
    assert urls == ["http://localhost:3000"]


def test_volume_support_windows_degraded() -> None:
    registry, _ = _registry()
    support, note = registry.matrix()["system.volume.set"]["windows"]
    assert support is Support.DEGRADED
    assert note.strip()
    dnd_linux, _ = registry.matrix()["system.dnd.set"]["linux"]
    assert dnd_linux is Support.DEGRADED
