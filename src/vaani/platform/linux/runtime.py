"""Assemble and run the Linux/X11 PlatformBundle."""
from __future__ import annotations

import os
import signal
import subprocess
import sys

from ...audio import AudioRecorderImpl
from ...codex import CodexRunner, ResultWindow
from ...config import Settings, sweep_audio_directory
from ...controller import Controller
from ...delivery import ClipboardDelivery
from ...groq import GroqClient
from ...history import HistoryStore
from ...hotkeys import HotkeyManager, XInputHotkeyManager
from ...observability import configure_logging
from ...secrets import SecretServiceKeyStore, effective_key
from ...x11 import X11Probe
from ..protocol import PlatformBundle, PlatformId
from .apps import LinuxAppLauncher
from .browser import LinuxBrowserLauncher
from .feedback import LinuxFeedback


def _reap_orphan_indicators() -> None:
    """Kill pills left by a previous crash/restart; visual-only, safe to kill."""
    patterns = (
        r"python -m vaani\.platform\.linux\.indicator_app",
        r"python -m vaani\.indicator",
    )
    for pattern in patterns:
        try:
            subprocess.run(
                ["pkill", "-f", pattern],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass


def build_linux(settings: Settings | None = None) -> PlatformBundle:
    settings = settings or Settings.from_home()
    settings.prepare()
    os.environ.setdefault("VAANI_AMPLITUDE_PATH", str(settings.amplitude_path))
    os.environ.setdefault("VAANI_INDICATOR_CONTROL", str(settings.indicator_control_path))
    return PlatformBundle(
        id=PlatformId.LINUX,
        settings=settings,
        recorder=AudioRecorderImpl(
            settings.audio_dir, amplitude_path=settings.amplitude_path
        ),
        hotkeys=_NoopHotkeys(),
        target=_NoopTarget(),
        delivery=_NoopDelivery(),
        apps=LinuxAppLauncher(),
        browser=LinuxBrowserLauncher(),
        feedback=LinuxFeedback(
            amplitude_path=settings.amplitude_path,
            control_path=settings.indicator_control_path,
            log_dir=settings.log_dir,
        ),
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
    _reap_orphan_indicators()
    sweep_audio_directory(settings.audio_dir)
    os.environ["VAANI_AMPLITUDE_PATH"] = str(settings.amplitude_path)
    os.environ["VAANI_INDICATOR_CONTROL"] = str(settings.indicator_control_path)
    logger = configure_logging(settings.log_dir, debug=settings.debug)
    try:
        from Xlib.display import Display

        display = Display()
    except Exception as exc:
        logger.error("startup failure category=shortcut detail=%s", type(exc).__name__)
        print(
            "[vaani] X11 display unavailable — global hotkeys need X11 "
            "(Wayland: use XWayland or an X11 session). "
            f"detail={type(exc).__name__}",
            file=sys.stderr,
            flush=True,
        )
        return 2

    probe = X11Probe(display)
    delivery = ClipboardDelivery(target=probe)
    recorder = AudioRecorderImpl(
        settings.audio_dir, amplitude_path=settings.amplitude_path
    )
    history = HistoryStore(settings.history_db)
    feedback = LinuxFeedback(
        amplitude_path=settings.amplitude_path,
        control_path=settings.indicator_control_path,
        log_dir=settings.log_dir,
    )
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
        indicator_control_path=settings.indicator_control_path,
        browser_launcher=browser,
        app_launcher=apps,
    )
    assistant = CodexRunner()

    def show_assistant_result(text: str) -> None:
        message = (text or "").strip() or "Assistant returned no output."
        feedback.notify("paste", message[:160])

    controller.codex = assistant
    controller.result_window = ResultWindow(show_assistant_result)

    def on_hotkey_press(mode: str) -> None:
        # Hold-to-talk: press starts. Block entirely while PROCESSING.
        from ...types import AppState

        if controller.state is AppState.PROCESSING:
            logger.info("event=input_blocked reason=processing source=press")
            return
        controller.trigger(mode)

    def on_hotkey_release(_mode: str) -> None:
        from ...types import AppState

        if controller.state is AppState.PROCESSING:
            return
        # Release stops capture; pill switches to processing animation.
        controller.stop()

    def assistant_trigger(_signum=None, _frame=None):
        on_hotkey_press("assistant")

    hotkeys = HotkeyManager(
        on_hotkey_press, display=display, on_release=on_hotkey_release
    )
    controller.hotkeys = hotkeys
    escape_monitor = XInputHotkeyManager(
        on_hotkey_press, on_cancel=controller.cancel
    )
    try:
        log_path = settings.log_dir / "vaani.log"
        session_type = os.environ.get("XDG_SESSION_TYPE", "unknown")
        print(f"[vaani] log file: {log_path}", flush=True)
        print(
            f"[vaani] debug={'on' if settings.debug else 'off'} (use --debug)",
            flush=True,
        )
        print(f"[vaani] session={session_type} (X11 hotkeys; Wayland needs XWayland)", flush=True)
        logger.info(
            "event=startup_linux log=%s debug=%s session=%s",
            log_path,
            settings.debug,
            session_type,
        )
        hotkeys.register()
        escape_monitor.register()
        logger.info(
            "startup complete; hold Ctrl+Space to dictate (release to stop); "
            "Ctrl+Shift+Space literal; Ctrl+Alt+Space assistant; Esc cancels"
        )
        print(
            "Vaani hotkeys ready (hold-to-talk):\n"
            "  Hold Ctrl+Space           → smart dictation\n"
            "  Hold Ctrl+Shift+Space     → literal\n"
            "  Hold Ctrl+Alt+Space       → assistant\n"
            "  Esc                       → cancel\n"
            "Release the chord to stop — pill stays up while processing.",
            flush=True,
        )
        signal.signal(signal.SIGINT, lambda *_: controller.shutdown())
        signal.signal(signal.SIGTERM, lambda *_: controller.shutdown())
        signal.signal(signal.SIGUSR1, lambda *_: on_hotkey_press("smart"))
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
        print(
            f"[vaani] runtime failure: {type(exc).__name__}: {exc}",
            file=sys.stderr,
            flush=True,
        )
        controller.shutdown()
        return 1
    finally:
        escape_monitor.unregister()
        try:
            hotkeys.unregister()
        except Exception:
            pass
        try:
            display.close()
        except Exception:
            pass
        logger.info("event=shutdown_linux")
    return 0
