"""Assemble and run the Linux/X11 PlatformBundle."""
from __future__ import annotations

import os
import signal
import subprocess

from ...audio import AudioRecorderImpl
from ...codex import CodexRunner, ResultWindow
from ...config import Settings, sweep_audio_directory
from ...controller import Controller
from ...delivery import ClipboardDelivery
from ...feedback import Feedback
from ...groq import GroqClient
from ...history import HistoryStore
from ...hotkeys import HotkeyManager, XInputHotkeyManager
from ...observability import configure_logging
from ...secrets import SecretServiceKeyStore, effective_key
from ...x11 import X11Probe
from ..protocol import PlatformBundle, PlatformId
from .apps import LinuxAppLauncher
from .browser import LinuxBrowserLauncher


def build_linux(settings: Settings | None = None) -> PlatformBundle:
    settings = settings or Settings.from_home()
    settings.prepare()
    os.environ.setdefault("VAANI_AMPLITUDE_PATH", str(settings.amplitude_path))
    return PlatformBundle(
        id=PlatformId.LINUX,
        settings=settings,
        recorder=AudioRecorderImpl(settings.audio_dir),
        hotkeys=_NoopHotkeys(),
        target=_NoopTarget(),
        delivery=_NoopDelivery(),
        apps=LinuxAppLauncher(),
        browser=LinuxBrowserLauncher(),
        feedback=Feedback(),
        key_store=SecretServiceKeyStore(),
        run=lambda _controller: run_linux(settings),
    )


class _NoopHotkeys:
    def register(self) -> None:
        return None

    def unregister(self) -> None:
        return None


class _NoopTarget:
    def snapshot(self):
        return None

    def unchanged(self, before) -> bool:
        return False


class _NoopDelivery:
    def deliver(self, text: str, *, snapshot=None):
        raise RuntimeError("use platform.run() to construct live Linux delivery")


def run_linux(settings: Settings) -> int:
    # Reap indicators left by a previous crash/restart before starting a new
    # session; the indicator is purely visual and has no work to preserve.
    subprocess.run(
        ["pkill", "-f", r"python -m vaani\.indicator"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    sweep_audio_directory(settings.audio_dir)
    os.environ["VAANI_AMPLITUDE_PATH"] = str(settings.amplitude_path)
    logger = configure_logging(settings.log_dir, debug=settings.debug)
    try:
        from Xlib.display import Display

        display = Display()
    except Exception as exc:
        logger.error("startup failure category=shortcut detail=%s", type(exc).__name__)
        return 2

    probe = X11Probe(display)
    delivery = ClipboardDelivery(target=probe)
    recorder = AudioRecorderImpl(settings.audio_dir)
    history = HistoryStore(settings.history_db)
    feedback = Feedback()
    groq = GroqClient()
    store = SecretServiceKeyStore()
    apps = LinuxAppLauncher()
    browser = LinuxBrowserLauncher()
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
        subprocess.run(
            ["notify-send", "Vaani assistant", text[:1800]],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    controller.codex = assistant
    controller.result_window = ResultWindow(show_assistant_result)

    def assistant_trigger(_signum=None, _frame=None):
        controller.handle_hotkey("assistant")

    hotkeys = HotkeyManager(controller.handle_hotkey, display=display)
    controller.hotkeys = hotkeys
    escape_monitor = XInputHotkeyManager(controller.handle_hotkey, on_cancel=controller.cancel)
    try:
        hotkeys.register()
        escape_monitor.register()
        logger.info(
            "startup complete; Ctrl+Space toggles dictation; "
            "Ctrl+Shift+Space toggles literal mode"
        )
        signal.signal(signal.SIGINT, lambda *_: controller.shutdown())
        signal.signal(signal.SIGTERM, lambda *_: controller.shutdown())
        signal.signal(signal.SIGUSR1, lambda *_: controller.handle_hotkey("smart"))
        signal.signal(signal.SIGUSR2, lambda *_: controller.cancel())
        assistant_signal = getattr(
            signal, "SIGUSR3", getattr(signal, "SIGRTMIN", signal.SIGUSR1 + 2)
        )
        signal.signal(assistant_signal, assistant_trigger)
        while not controller._shutdown:
            event = display.next_event()
            hotkeys.handle_event(event)
    except KeyboardInterrupt:
        controller.shutdown()
    except Exception as exc:
        logger.error("runtime failure category=shortcut detail=%s", type(exc).__name__)
        controller.shutdown()
        return 1
    finally:
        escape_monitor.unregister()
        try:
            display.close()
        except Exception:
            pass
    return 0
