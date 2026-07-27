"""Run Vaani on Wayland: portal hotkeys + wl-clipboard/wtype delivery.

Mirrors ``platform.linux.runtime._run_x11`` structurally (same Controller
wiring, same on_hotkey_press/on_hotkey_release contract, same signals for
the vaani-toggle/vaani-assistant/vaani-cancel launcher scripts) but has no
Xlib event loop — the portal's D-Bus listener runs on its own thread (see
``hotkeys.PortalHotkeyManager``), so the main thread just waits for
shutdown, the same pattern the Windows backend uses for its
pynput-listener-owns-a-thread setup.
"""
from __future__ import annotations

import os
import signal
import sys
import time

from ....codex import CodexRunner, ResultWindow
from ....config import Settings, sweep_audio_directory
from ....controller import Controller
from ....groq import GroqClient
from ....history import HistoryStore
from ....observability import configure_logging
from ....secrets import SecretServiceKeyStore, effective_key
from ...protocol import PlatformId
from ..apps import LinuxAppLauncher
from ..browser import LinuxBrowserLauncher
from ..feedback import LinuxFeedback
from ..runtime import _reap_orphan_indicators, _reap_orphan_mic
from .delivery import WaylandClipboardDelivery
from .hotkeys import PortalHotkeyManager
from .portal import PortalUnavailable


def run_wayland(settings: Settings) -> int:
    _reap_orphan_indicators()
    _reap_orphan_mic()
    sweep_audio_directory(settings.audio_dir)
    os.environ["VAANI_AMPLITUDE_PATH"] = str(settings.amplitude_path)
    os.environ["VAANI_INDICATOR_CONTROL"] = str(settings.indicator_control_path)
    logger = configure_logging(settings.log_dir, debug=settings.debug)

    from ....audio import AudioRecorderImpl

    delivery = WaylandClipboardDelivery()
    recorder = AudioRecorderImpl(settings.audio_dir, amplitude_path=settings.amplitude_path)
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
        logger.info("event=assistant_result_ui skipped=notify chars=%s", len(text or ""))

    controller.codex = assistant
    controller.result_window = ResultWindow(show_assistant_result)

    def on_hotkey_press(mode: str) -> None:
        from ....types import AppState

        if controller.state is AppState.PROCESSING:
            logger.info("event=input_blocked reason=processing source=press")
            return
        if controller.state is AppState.RECORDING:
            return
        controller.trigger(mode)

    def on_hotkey_release(_mode: str) -> None:
        from ....types import AppState

        if controller.state is AppState.PROCESSING:
            return
        controller.stop()

    def on_signal_toggle(_signum=None, _frame=None):
        # SIGUSR1 is stop-only so late pill Stop never ghost-starts when idle.
        from ....types import AppState

        if controller.state is AppState.RECORDING:
            controller.stop()
        else:
            logger.info(
                "event=signal_usr1_ignored state=%s reason=stop_only",
                getattr(controller.state, "name", controller.state),
            )

    def assistant_trigger(_signum=None, _frame=None):
        on_hotkey_press("assistant")

    hotkeys = PortalHotkeyManager(
        on_hotkey_press, on_release=on_hotkey_release, on_cancel=controller.cancel, logger=logger,
    )
    controller.hotkeys = hotkeys

    log_path = settings.log_dir / "vaani.log"
    print(f"[vaani] log file: {log_path}", flush=True)
    print(f"[vaani] debug={'on' if settings.debug else 'off'} (use --debug)", flush=True)
    print("[vaani] session=wayland (portal-based global shortcuts)", flush=True)
    logger.info("event=startup_linux backend=wayland log=%s debug=%s", log_path, settings.debug)

    try:
        hotkeys.register()
    except PortalUnavailable as exc:
        logger.error("startup failure category=shortcut detail=%s", exc)
        print(
            "[vaani] Wayland global shortcuts unavailable: "
            f"{exc}\n"
            "This compositor's xdg-desktop-portal doesn't implement "
            "GlobalShortcuts (needs GNOME 45+, KDE Plasma 6+, or another "
            "portal backend with that interface).",
            file=sys.stderr, flush=True,
        )
        return 2

    logger.info(
        "startup complete; hold Ctrl+Space to dictate (release to stop); "
        "Ctrl+Shift+Space literal; Ctrl+Alt+Space assistant "
        "(bound once via the system shortcut dialog)"
    )
    print(
        "Vaani hotkeys ready (hold-to-talk, Wayland portal):\n"
        "  Ctrl+Space           → smart dictation\n"
        "  Ctrl+Shift+Space     → literal\n"
        "  Ctrl+Alt+Space       → assistant\n"
        "Assign these once in the system shortcut dialog if prompted.\n"
        "No synthetic Esc-cancel on Wayland; release the chord to stop.\n"
        "Auto-paste works only on compositors with wtype support (e.g. "
        "Sway); elsewhere, text goes to the clipboard — press Ctrl+V.",
        flush=True,
    )

    signal.signal(signal.SIGINT, lambda *_: controller.shutdown())
    signal.signal(signal.SIGTERM, lambda *_: controller.shutdown())
    signal.signal(signal.SIGUSR1, on_signal_toggle)
    signal.signal(signal.SIGUSR2, lambda *_: controller.cancel())
    assistant_signal = getattr(signal, "SIGUSR3", getattr(signal, "SIGRTMIN", signal.SIGUSR1 + 2))
    signal.signal(assistant_signal, assistant_trigger)

    try:
        while not controller._shutdown:
            time.sleep(0.2)
    except KeyboardInterrupt:
        controller.shutdown()
    finally:
        try:
            hotkeys.unregister()
        except Exception:
            pass
        logger.info("event=shutdown_linux backend=wayland")
    return 0
