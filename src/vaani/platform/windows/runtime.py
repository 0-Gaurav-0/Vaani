"""Assemble and run the Windows PlatformBundle."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from typing import Any, Callable

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
            log_dir=settings.log_dir,
        ),
        key_store=SecretServiceKeyStore(),
        run=lambda _controller: run_windows(settings),
    )


class _NoopHotkeys:
    def register(self) -> None:
        return None

    def unregister(self) -> None:
        return None


def reap_orphan_indicators(
    *,
    runner: Callable[..., Any] = subprocess.run,
    platform: str | None = None,
) -> None:
    """Kill leftover indicator_app processes from a previous crash.

    Visual-only; safe to kill. No-op on non-Windows hosts (CI).
    """
    host = platform if platform is not None else sys.platform
    if host != "win32":
        return
    script = (
        "$marker = 'vaani.platform.windows.indicator_app';"
        "Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |"
        "Where-Object { $_.CommandLine -and $_.CommandLine -like ('*{0}*' -f $marker) } |"
        "ForEach-Object {"
        "  Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue"
        "}"
    )
    try:
        runner(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=8,
        )
    except Exception:
        pass


def run_windows(settings: Settings) -> int:
    """Run the Windows hotkey loop."""
    sweep_audio_directory(settings.audio_dir)
    os.environ["VAANI_AMPLITUDE_PATH"] = str(settings.amplitude_path)
    os.environ["VAANI_INDICATOR_CONTROL"] = str(settings.indicator_control_path)
    logger = configure_logging(settings.log_dir, debug=settings.debug)

    # Reap orphan pills from a previous crash; visual-only, safe to kill.
    try:
        reap_orphan_indicators()
    except Exception:
        pass

    target = WindowsTargetProbe()
    delivery = WindowsDelivery(target=target)
    recorder = WindowsAudioRecorder(
        settings.audio_dir, amplitude_path=settings.amplitude_path
    )
    history = HistoryStore(settings.history_db)
    feedback = WindowsFeedback(
        amplitude_path=settings.amplitude_path,
        control_path=settings.indicator_control_path,
        log_dir=settings.log_dir,
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
        message = (text or "").strip() or "Assistant returned no output."
        feedback.notify("paste", message[:160])

    controller.codex = assistant
    controller.result_window = ResultWindow(show_assistant_result)

    shutdown_event = threading.Event()

    def request_shutdown(*_args) -> None:
        controller.shutdown()
        shutdown_event.set()

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

    hotkeys = WindowsHotkeyService(
        on_hotkey_press,
        on_release=on_hotkey_release,
        on_cancel=controller.cancel,
        logger=logger,
    )
    controller.hotkeys = hotkeys

    try:
        from .trust import python_paths, setup_help_text

        log_path = settings.log_dir / "vaani.log"
        print(f"[vaani] log file: {log_path}", flush=True)
        print(f"[vaani] debug={'on' if settings.debug else 'off'} (use --debug)", flush=True)
        logger.info("event=startup_windows log=%s debug=%s", log_path, settings.debug)
        logger.info("hotkey interpreter paths: %s", ", ".join(python_paths()))
        print(setup_help_text(brief=True), flush=True)
        hotkeys.register()
        logger.info(
            "startup complete; hold Ctrl+Space to dictate "
            "(release to stop); Ctrl+Shift+Space literal; "
            "Ctrl+Alt+Space assistant"
        )
        signal.signal(signal.SIGINT, request_shutdown)
        try:
            signal.signal(signal.SIGTERM, request_shutdown)
        except (AttributeError, ValueError):
            # SIGTERM is not always meaningful on Windows console hosts.
            pass
        while not controller._shutdown and not shutdown_event.is_set():
            time.sleep(0.2)
    except KeyboardInterrupt:
        request_shutdown()
    except Exception as exc:
        logger.exception("runtime failure category=shortcut detail=%s", type(exc).__name__)
        print(f"[vaani] runtime failure: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        request_shutdown()
        return 1
    finally:
        try:
            hotkeys.unregister()
        except Exception:
            pass
        logger.info("event=shutdown_windows")
    return 0
