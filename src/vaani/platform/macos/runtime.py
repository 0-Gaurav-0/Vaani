"""Assemble and run the macOS PlatformBundle."""
from __future__ import annotations

import os
import signal
import sys
import threading

from ...codex import CodexRunner, ResultWindow
from ...config import Settings, sweep_audio_directory
from ...controller import Controller
from ...groq import GroqClient
from ...history import HistoryStore
from ...observability import configure_logging
from ...secrets import SecretServiceKeyStore, effective_key
from ..protocol import PlatformBundle, PlatformId
from .apps import MacAppLauncher
from .audio import MacAudioRecorder
from .browser import MacBrowserLauncher
from .delivery import MacClipboardDelivery
from .feedback import MacFeedback
from .hotkeys import HotkeyService
from .target import MacTargetProbe


def build_macos(settings: Settings | None = None) -> PlatformBundle:
    settings = settings or Settings.from_home()
    settings.prepare()
    os.environ.setdefault("VAANI_AMPLITUDE_PATH", str(settings.amplitude_path))
    target = MacTargetProbe()
    delivery = MacClipboardDelivery(target=target)
    feedback = MacFeedback()
    apps = MacAppLauncher()
    browser = MacBrowserLauncher()
    recorder = MacAudioRecorder(settings.audio_dir)
    hotkeys = HotkeyService(lambda _mode: None)
    return PlatformBundle(
        id=PlatformId.MACOS,
        settings=settings,
        recorder=recorder,
        hotkeys=hotkeys,
        target=target,
        delivery=delivery,
        apps=apps,
        browser=browser,
        feedback=feedback,
        key_store=SecretServiceKeyStore(),
        run=lambda _controller: run_macos(settings),
    )


def run_macos(settings: Settings) -> int:
    sweep_audio_directory(settings.audio_dir)
    os.environ["VAANI_AMPLITUDE_PATH"] = str(settings.amplitude_path)
    logger = configure_logging(settings.log_dir, debug=settings.debug)

    target = MacTargetProbe()
    delivery = MacClipboardDelivery(target=target)
    recorder = MacAudioRecorder(settings.audio_dir)
    history = HistoryStore(settings.history_db)
    feedback = MacFeedback()
    groq = GroqClient()
    store = SecretServiceKeyStore()
    apps = MacAppLauncher()
    browser = MacBrowserLauncher()
    controller = Controller(
        recorder=recorder,
        groq=groq,
        delivery=delivery,
        history=history,
        feedback=feedback,
        key_provider=lambda: effective_key(store).value,
        amplitude_path=settings.amplitude_path,
        browser_launcher=browser,
        app_launcher=apps,
    )
    assistant = CodexRunner()

    def show_assistant_result(text: str) -> None:
        feedback.notify("paste", (text or "")[:160])

    controller.codex = assistant
    controller.result_window = ResultWindow(show_assistant_result)

    shutdown_event = threading.Event()

    def request_shutdown(*_args) -> None:
        controller.shutdown()
        shutdown_event.set()

    hotkeys = HotkeyService(
        controller.handle_hotkey,
        on_cancel=controller.cancel,
        logger=logger,
    )
    controller.hotkeys = hotkeys

    try:
        from .trust import python_paths

        log_path = settings.log_dir / "vaani.log"
        print(f"[vaani] log file: {log_path}", flush=True)
        print(f"[vaani] debug={'on' if settings.debug else 'off'} (use --debug)", flush=True)
        logger.info("event=startup_macos log=%s debug=%s", log_path, settings.debug)
        logger.info("hotkey interpreter paths: %s", ", ".join(python_paths()))
        hotkeys.register()
        logger.info(
            "startup complete; Control+Option+V toggles dictation; "
            "Control+Option+Shift+V toggles literal; "
            "Control+Option+A toggles assistant"
        )
        signal.signal(signal.SIGINT, request_shutdown)
        signal.signal(signal.SIGTERM, request_shutdown)
        # Carbon hotkey events must be pumped on the main thread.
        while not controller._shutdown and not shutdown_event.is_set():
            hotkeys.pump(0.25)
    except KeyboardInterrupt:
        request_shutdown()
    except Exception as exc:
        logger.exception("runtime failure category=shortcut detail=%s", type(exc).__name__)
        print(f"[vaani] runtime failure: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        request_shutdown()
        return 1
    finally:
        hotkeys.unregister()
        logger.info("event=shutdown_macos")
    return 0
