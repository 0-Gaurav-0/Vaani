"""Unit tests for macOS platform adapters (mocked; no Accessibility required)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from vaani.delivery import DeliveryStatus
from vaani.platform.protocol import AppTarget, FocusSnapshot, PlatformId


def test_resolve_app_requires_action_word():
    from vaani.platform.macos.apps import resolve_app

    assert resolve_app("Terminal") is None
    assert resolve_app("please open Terminal") is not None
    target = resolve_app("launch calculator")
    assert target is not None
    assert target.name == "Calculator"
    assert target.native_name == "Calculator"


def test_resolve_app_ignores_browser_phrases():
    from vaani.platform.macos.apps import resolve_app

    assert resolve_app("open terminal in chrome") is None
    assert resolve_app("open website for notes") is None


def test_launch_app_uses_open_dash_a():
    from vaani.platform.macos.apps import launch_app

    calls: list[list[str]] = []

    def runner(args, **_kwargs):
        calls.append(list(args))
        return SimpleNamespace(returncode=0)

    target = AppTarget("Cursor", ("Cursor",), native_name="Cursor")
    message = launch_app(target, runner=runner)
    assert message == "Opened Cursor."
    assert calls == [["open", "-a", "Cursor"]]


def test_browser_prefers_brave_then_chrome_then_default():
    from vaani.platform.macos.browser import MacBrowserLauncher

    attempts: list[list[str]] = []

    def runner(args, **_kwargs):
        attempts.append(list(args))
        # Fail Brave, succeed Chrome.
        if args[2] == "Brave Browser":
            return SimpleNamespace(returncode=1)
        return SimpleNamespace(returncode=0)

    launcher = MacBrowserLauncher(runner=runner)
    assert launcher.open("https://example.com", prefer="brave") == "Opened browser."
    assert attempts[0] == ["open", "-a", "Brave Browser", "https://example.com"]
    assert attempts[1] == ["open", "-a", "Google Chrome", "https://example.com"]


def test_browser_prefer_chrome_first():
    from vaani.platform.macos.browser import MacBrowserLauncher

    attempts: list[list[str]] = []

    def runner(args, **_kwargs):
        attempts.append(list(args))
        return SimpleNamespace(returncode=0)

    launcher = MacBrowserLauncher(runner=runner)
    assert launcher.open("https://example.com", prefer="chrome") == "Opened browser."
    assert attempts[0][2] == "Google Chrome"


def test_delivery_clipboard_only_on_paste_failure():
    from vaani.platform.macos.delivery import MacClipboardDelivery

    class FakeClipboard:
        def __init__(self):
            self.value = ""

        def set_text(self, text: str) -> None:
            self.value = text

        def read_text(self) -> str:
            return self.value

    class FailPaster:
        def paste(self) -> None:
            raise RuntimeError("Accessibility denied")

    class StableTarget:
        def unchanged(self, before) -> bool:
            return True

    delivery = MacClipboardDelivery(
        clipboard=FakeClipboard(),
        target=StableTarget(),
        paster=FailPaster(),
        sleep=lambda _s: None,
    )
    snap = FocusSnapshot(token="TextEdit|1")
    assert delivery.deliver("hello", snapshot=snap) is DeliveryStatus.CLIPBOARD_ONLY


def test_delivery_paste_dispatched_when_paste_ok():
    from vaani.platform.macos.delivery import MacClipboardDelivery

    class FakeClipboard:
        def __init__(self):
            self.value = ""

        def set_text(self, text: str) -> None:
            self.value = text

        def read_text(self) -> str:
            return self.value

    class OkPaster:
        def __init__(self):
            self.called = False

        def paste(self) -> None:
            self.called = True

    class StableTarget:
        def unchanged(self, before) -> bool:
            return True

    paster = OkPaster()
    delivery = MacClipboardDelivery(
        clipboard=FakeClipboard(),
        target=StableTarget(),
        paster=paster,
        sleep=lambda _s: None,
    )
    status = delivery.deliver("hello", snapshot=FocusSnapshot(token="a|1"))
    assert status is DeliveryStatus.PASTE_DISPATCHED
    assert paster.called


def test_delivery_clipboard_only_on_focus_change():
    from vaani.platform.macos.delivery import MacClipboardDelivery

    class FakeClipboard:
        def __init__(self):
            self.value = ""

        def set_text(self, text: str) -> None:
            self.value = text

        def read_text(self) -> str:
            return self.value

    class MovingTarget:
        def unchanged(self, before) -> bool:
            return False

    delivery = MacClipboardDelivery(
        clipboard=FakeClipboard(),
        target=MovingTarget(),
        paster=MagicMock(),
        sleep=lambda _s: None,
    )
    status = delivery.deliver("hello", snapshot=FocusSnapshot(token="a|1"))
    assert status is DeliveryStatus.CLIPBOARD_ONLY


def test_target_snapshot_parses_osascript():
    from vaani.platform.macos.target import MacTargetProbe

    def runner(args, **_kwargs):
        assert args[0] == "osascript"
        return SimpleNamespace(returncode=0, stdout="TextEdit, 4242\n")

    probe = MacTargetProbe(runner=runner)
    snap = probe.snapshot()
    assert snap == FocusSnapshot(token="TextEdit|4242")
    assert probe.unchanged(snap) is True


def test_build_macos_returns_macos_bundle(tmp_path, monkeypatch):
    from vaani.config import Settings
    from vaani.platform.macos.runtime import build_macos

    settings = Settings.from_home(home=tmp_path)
    # Avoid real Keychain / mic during assembly.
    monkeypatch.setattr(
        "vaani.platform.macos.runtime.SecretServiceKeyStore",
        lambda: MagicMock(name="keystore"),
    )
    bundle = build_macos(settings)
    assert bundle.id is PlatformId.MACOS
    assert bundle.recorder is not None
    assert bundle.apps is not None
    assert bundle.browser is not None
    assert bundle.delivery is not None
    assert bundle.target is not None
    assert bundle.feedback is not None
    assert callable(bundle.run)


def test_hotkey_service_register_unregister():
    from vaani.platform.macos.hotkeys import HotkeyService

    triggers: list[str] = []
    cancels: list[str] = []
    started = {"value": False}
    stopped = {"value": False}

    class FakeListener:
        def __init__(self, mapping):
            self.mapping = mapping

        def start(self):
            started["value"] = True

        def stop(self):
            stopped["value"] = True

        def join(self):
            return None

    service = HotkeyService(
        lambda mode: triggers.append(mode),
        on_cancel=lambda: cancels.append("cancel"),
        listener_factory=FakeListener,
    )
    service.register()
    assert started["value"]
    # Simulate chords.
    # Find callbacks by inspecting the factory-built listener.
    listener = service._listener
    assert "<ctrl>+<alt>+v" in listener.mapping
    listener.mapping["<ctrl>+<alt>+v"]()
    listener.mapping["<esc>"]()
    assert triggers == ["smart"]
    assert cancels == ["cancel"]
    service.unregister()
    assert stopped["value"]
