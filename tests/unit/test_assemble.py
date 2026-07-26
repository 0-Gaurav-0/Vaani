"""Tests for shared controller assembly (T0.1)."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from vaani.assemble import Assembly, assemble
from vaani.codex import CodexRunner, ResultWindow
from vaani.controller import Controller
from vaani.groq import GroqClient
from vaani.history import HistoryStore
from vaani.platform.protocol import PlatformBundle, PlatformId


class _Rec:
    pass


class _Delivery:
    def deliver(self, text: str, *, snapshot=None):
        return "ok"


class _Hotkeys:
    def register(self) -> None:
        return None

    def unregister(self) -> None:
        return None


class _Target:
    def snapshot(self):
        return None

    def unchanged(self, before) -> bool:
        return False


class _Apps:
    def resolve(self, command: str):
        return None

    def launch(self, target) -> str:
        return "launched"


class _Browser:
    def open(self, url: str, *, prefer: str | None = None) -> str:
        return "opened"


class _Feedback:
    def __init__(self) -> None:
        self.notifications: list[tuple[str, str]] = []

    def play(self, cue: str) -> bool:
        return True

    def notify(self, category: str, message: str = "") -> None:
        self.notifications.append((category, message))


class _KeyStore:
    def get(self) -> str | None:
        return "test-key"

    def set(self, value: str) -> None:
        return None

    def remove(self) -> None:
        return None


def _fake_bundle(tmp_path: Path, *, delivery=None, feedback=None) -> PlatformBundle:
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    settings = SimpleNamespace(
        history_db=tmp_path / "history.sqlite3",
        amplitude_path=tmp_path / "amplitude",
        indicator_control_path=tmp_path / "indicator_control.json",
        log_dir=log_dir,
        debug=False,
    )
    return PlatformBundle(
        id=PlatformId.LINUX,
        settings=settings,
        recorder=_Rec(),
        hotkeys=_Hotkeys(),
        target=_Target(),
        delivery=delivery or _Delivery(),
        apps=_Apps(),
        browser=_Browser(),
        feedback=feedback or _Feedback(),
        key_store=_KeyStore(),
        run=lambda _controller: 0,
    )


def test_assemble_wires_controller_from_fake_bundle(tmp_path):
    feedback = _Feedback()
    bundle = _fake_bundle(tmp_path, feedback=feedback)

    assembly = assemble(bundle)

    assert isinstance(assembly, Assembly)
    assert isinstance(assembly.controller, Controller)
    assert isinstance(assembly.history, HistoryStore)
    assert isinstance(assembly.groq, GroqClient)
    assert assembly.logger is not None

    controller = assembly.controller
    assert controller.recorder is bundle.recorder
    assert controller.delivery is bundle.delivery
    assert controller.history is assembly.history
    assert controller.groq is assembly.groq
    assert controller.feedback is bundle.feedback
    assert controller.app_launcher is bundle.apps
    assert controller.browser_launcher is bundle.browser
    assert isinstance(controller.codex, CodexRunner)
    assert isinstance(controller.result_window, ResultWindow)

    controller.result_window.sink("hello from assistant")
    assert feedback.notifications == [("paste", "hello from assistant")]


def test_assemble_delivery_override(tmp_path):
    bundle = _fake_bundle(tmp_path)
    override = _Delivery()

    assembly = assemble(bundle, delivery=override)

    assert assembly.controller.delivery is override
    assert assembly.controller.delivery is not bundle.delivery


def test_runtimes_call_assemble_and_drop_duplicated_wiring():
    root = Path(__file__).resolve().parents[2] / "src" / "vaani" / "platform"
    for os_name in ("linux", "macos", "windows"):
        text = (root / os_name / "runtime.py").read_text(encoding="utf-8")
        assert "assemble(" in text, f"{os_name} runtime must call assemble("
        assert "HistoryStore(" not in text, f"{os_name} must not construct HistoryStore"
        assert "Controller(" not in text, f"{os_name} must not construct Controller"
