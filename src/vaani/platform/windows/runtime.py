"""Assemble and run the Windows PlatformBundle."""
from __future__ import annotations

import os
import signal
import time

from ...codex import CodexRunner, ResultWindow
from ...config import Settings, sweep_audio_directory
from ...controller import Controller
from ...groq import GroqClient
from ...history import HistoryStore
from ...observability import configure_logging
from ...secrets import SecretServiceKeyStore, effective_key
from ..protocol import PlatformBundle, PlatformId
from .apps import WindowsAppLauncher
from .audio import WindowsAudioRecorder
from .browser import WindowsBrowserLauncher
from .delivery import WindowsDelivery
from .feedback import WindowsFeedback
from .hotkeys import WindowsHotkeyService
from .target import WindowsTargetProbe


def build_windows(settings: Settings | None = None) -> PlatformBundle:
    settings = settings or Settings.from_home()
    settings.prepare()
    os.environ.setdefault("VAANI_AMPLITUDE_PATH", str(settings.amplitude_path))
    os.environ.setdefault("VAANI_INDICATOR_CONTROL", str(settings.indicator_control_path))
    target = WindowsTargetProbe()
    return PlatformBundle(
        id=PlatformId.WINDOWS,
        settings=settings,
        recorder=WindowsAudioRecorder(
            settings.audio_dir, amplitude_path=settings.amplitude_path
        ),
        hotkeys=_NoopHotkeys(),
        target=target,
        delivery=WindowsDelivery(target=target),
        apps=WindowsAppLauncher(),
        browser=WindowsBrowserLauncher(),
        feedback=WindowsFeedback(
            amplitude_path=settings.amplitude_path,
            control_path=settings.indicator_control_path,
        ),
        key_store=SecretServiceKeyStore(),
        run=lambda _controller: run_windows(settings),
    )


class _NoopHotkeys:
    def register(self) -> None:
        return None

    def unregister(self) -> None:
        return None


def run_windows(settings: Settings) -> int:
    """Run the Windows hotkey loop."""
    sweep_audio_directory(settings.audio_dir)
    os.environ["VAANI_AMPLITUDE_PATH"] = str(settings.amplitude_path)
    os.environ["VAANI_INDICATOR_CONTROL"] = str(settings.indicator_control_path)
    logger = configure_logging(settings.log_dir, debug=settings.debug)

    target = WindowsTargetProbe()
    delivery = WindowsDelivery(target=target)
    recorder = WindowsAudioRecorder(
        settings.audio_dir, amplitude_path=settings.amplitude_path
    )
    history = HistoryStore(settings.history_db)
    feedback = WindowsFeedback(
        amplitude_path=settings.amplitude_path,
        control_path=settings.indicator_control_path,
    )
    groq = GroqClient()
    store = SecretServiceKeyStore()
    apps = WindowsAppLauncher()
    browser = WindowsBrowserLauncher()
    controller = Controller(
        recorder=recorder,
        groq=groq,
        delivery=delivery,
        history=history,
        feedback=feedback,
        key_provider=lambda: effective_key(store).value,
        amplitude_path=settings.amplitude_path,
        indicator_control_path=settings.indicator_control_path,
        browser_launcher=browser,
        app_launcher=apps,
    )
    assistant = CodexRunner()

    def show_assistant_result(text: str) -> None:
        feedback.notify("paste", text[:160])

    controller.codex = assistant
    controller.result_window = ResultWindow(show_assistant_result)

    hotkeys = WindowsHotkeyService(controller.handle_hotkey, on_cancel=controller.cancel)
    controller.hotkeys = hotkeys
    try:
        hotkeys.register()
        logger.info(
            "startup complete; Ctrl+Space toggles dictation; "
            "Ctrl+Shift+Space toggles literal mode; "
            "Ctrl+Alt+Space starts assistant; Esc cancels"
        )
        signal.signal(signal.SIGINT, lambda *_: controller.shutdown())
        try:
            signal.signal(signal.SIGTERM, lambda *_: controller.shutdown())
        except (AttributeError, ValueError):
            # SIGTERM is not always meaningful on Windows console hosts.
            pass
        while not controller._shutdown:
            time.sleep(0.2)
    except KeyboardInterrupt:
        controller.shutdown()
    except Exception as exc:
        logger.error("runtime failure category=shortcut detail=%s", type(exc).__name__)
        controller.shutdown()
        return 1
    finally:
        try:
            hotkeys.unregister()
        except Exception:
            pass
    return 0
