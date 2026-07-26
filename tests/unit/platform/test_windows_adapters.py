"""Unit tests for Windows platform adapters (mocked; safe on macOS/Linux CI)."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from vaani.delivery import DeliveryStatus
from vaani.platform.protocol import AppTarget, FocusSnapshot, PlatformId
from vaani.platform.windows.apps import WindowsAppLauncher, launch_app, resolve_app
from vaani.platform.windows.browser import WindowsBrowserLauncher
from vaani.platform.windows.delivery import WindowsDelivery
from vaani.platform.windows.feedback import WindowsFeedback
from vaani.platform.windows.hotkeys import WindowsHotkeyService
from vaani.platform.windows.target import WindowsTargetProbe


def test_resolve_app_requires_open_verb():
    assert resolve_app("notepad") is None
    target = resolve_app("open notepad")
    assert target is not None
    assert target.name == "Notepad"


def test_resolve_app_catalog_aliases():
    assert resolve_app("launch windows terminal").name == "Windows Terminal"
    assert resolve_app("start calculator").name == "Calculator"
    assert resolve_app("show explorer").name == "File Explorer"
    assert resolve_app("open cursor").name == "Cursor"
    assert resolve_app("open visual studio code").name == "Visual Studio Code"
    assert resolve_app("open brave website") is None


def test_launch_app_uses_resolved_executable(monkeypatch):
    calls: list[list[str]] = []

    def fake_popen(args, **_kwargs):
        calls.append(list(args))
        return SimpleNamespace(poll=lambda: None)

    monkeypatch.setattr(
        "vaani.platform.windows.apps._which",
        lambda name: r"C:\Windows\System32\notepad.exe" if "notepad" in name else None,
    )
    target = AppTarget("Notepad", ("notepad.exe",), native_name="notepad")
    message = launch_app(target, popen=fake_popen, startfile=None)
    assert message == "Opened Notepad."
    assert calls == [[r"C:\Windows\System32\notepad.exe"]]


def test_windows_app_launcher_roundtrip(monkeypatch):
    launcher = WindowsAppLauncher()
    monkeypatch.setattr(
        "vaani.platform.windows.apps.launch_app",
        lambda target: f"Opened {target.name}.",
    )
    target = launcher.resolve("open calc")
    assert target is not None
    assert launcher.launch(target) == "Opened Calculator."


def test_browser_prefers_brave_then_falls_back():
    calls: list[list[str]] = []

    def fake_popen(args, **_kwargs):
        calls.append(list(args))
        raise OSError("missing")

    opened: list[str] = []

    launcher = WindowsBrowserLauncher(
        popen=fake_popen,
        open_url=lambda url: opened.append(url) or True,
        sleeper=lambda _s: None,
        brave_finder=lambda: [r"C:\Brave\brave.exe"],
        chrome_finder=lambda: [r"C:\Chrome\chrome.exe"],
    )
    assert launcher.open("https://example.com", prefer="brave") == "Opened browser."
    assert calls[0][0].endswith("brave.exe")
    assert opened == ["https://example.com"]


def test_browser_opens_chrome_when_preferred():
    procs: list[list[str]] = []

    def fake_popen(args, **_kwargs):
        procs.append(list(args))
        return SimpleNamespace(poll=lambda: None)

    launcher = WindowsBrowserLauncher(
        popen=fake_popen,
        open_url=lambda _url: False,
        sleeper=lambda _s: None,
        brave_finder=lambda: [r"C:\Brave\brave.exe"],
        chrome_finder=lambda: [r"C:\Chrome\chrome.exe"],
    )
    assert launcher.open("about:blank", prefer="chrome").startswith("Opened")
    assert procs[0][0].endswith("chrome.exe")


def test_target_snapshot_uses_hwnd_token():
    probe = WindowsTargetProbe(foreground=lambda: 4242)
    snap = probe.snapshot()
    assert snap == FocusSnapshot(token="4242")
    assert probe.unchanged(snap) is True
    probe2 = WindowsTargetProbe(foreground=lambda: 7)
    assert probe2.unchanged(snap) is False


def test_delivery_paste_success():
    clipboard = {"text": ""}

    delivery = WindowsDelivery(
        target=WindowsTargetProbe(foreground=lambda: 1),
        set_clipboard=lambda text: clipboard.__setitem__("text", text),
        get_clipboard=lambda: clipboard["text"],
        paste=lambda: None,
        sleep=lambda _s: None,
    )
    snap = FocusSnapshot(token="1")
    assert delivery.deliver("hello", snapshot=snap) is DeliveryStatus.PASTE_DISPATCHED


def test_delivery_focus_change_is_clipboard_only():
    clipboard = {"text": ""}
    hwnd = {"value": 1}

    delivery = WindowsDelivery(
        target=WindowsTargetProbe(foreground=lambda: hwnd["value"]),
        set_clipboard=lambda text: clipboard.__setitem__("text", text),
        get_clipboard=lambda: clipboard["text"],
        paste=lambda: (_ for _ in ()).throw(AssertionError("paste should not run")),
        sleep=lambda _s: None,
    )
    snap = FocusSnapshot(token="1")
    hwnd["value"] = 99
    assert delivery.deliver("hello", snapshot=snap) is DeliveryStatus.CLIPBOARD_ONLY


def test_delivery_paste_failure_falls_back():
    clipboard = {"text": ""}

    delivery = WindowsDelivery(
        target=WindowsTargetProbe(foreground=lambda: 5),
        set_clipboard=lambda text: clipboard.__setitem__("text", text),
        get_clipboard=lambda: clipboard["text"],
        paste=lambda: (_ for _ in ()).throw(RuntimeError("blocked")),
        sleep=lambda _s: None,
    )
    assert (
        delivery.deliver("hello", snapshot=FocusSnapshot(token="5"))
        is DeliveryStatus.CLIPBOARD_ONLY
    )


def test_hotkeys_register_mapping():
    triggers: list[str] = []
    cancels: list[str] = []
    started = {"value": False}

    class FakeListener:
        def __init__(self, mapping):
            self.mapping = mapping

        def start(self):
            started["value"] = True

        def stop(self):
            started["value"] = False

    service = WindowsHotkeyService(
        triggers.append,
        on_cancel=lambda: cancels.append("cancel"),
        listener_factory=FakeListener,
    )
    service.register()
    assert started["value"] is True
    assert "<ctrl>+<space>" in service._listener.mapping
    assert "<esc>" in service._listener.mapping
    service._listener.mapping["<ctrl>+<space>"]()
    service._listener.mapping["<esc>"]()
    assert triggers == ["smart"]
    assert cancels == ["cancel"]
    service.unregister()
    assert started["value"] is False


def test_feedback_console_fallback():
    lines: list[str] = []

    class Boom:
        def __call__(self, *_args, **_kwargs):
            raise OSError("no powershell")

    fb = WindowsFeedback(runner=Boom(), beeper=lambda: None, printer=lines.append)
    fb.notify("target", "")
    assert lines and "copied only" in lines[0]


def test_build_windows_bundle_id(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    from vaani.config import Settings
    from vaani.platform.windows.runtime import build_windows

    settings = Settings.from_home(tmp_path, platform="windows")
    bundle = build_windows(settings)
    assert bundle.id is PlatformId.WINDOWS
    assert bundle.apps.resolve("open notepad") is not None
    assert "amplitude" in str(bundle.settings.amplitude_path)


def test_codex_stop_process_uses_terminate_on_windows(monkeypatch):
    from vaani import codex as codex_mod

    monkeypatch.setattr(codex_mod.os, "name", "nt")
    calls: list[str] = []
    proc = SimpleNamespace(
        poll=lambda: None,
        terminate=lambda: calls.append("terminate"),
        kill=lambda: calls.append("kill"),
        pid=123,
    )
    codex_mod._stop_process(proc, forceful=False)
    assert calls == ["terminate"]
    calls.clear()
    codex_mod._stop_process(proc, forceful=True)
    assert calls == ["kill"]
