"""Assemble and run the macOS PlatformBundle."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading

from ...assemble import assemble
from ...config import Settings, sweep_audio_directory
from ...observability import configure_logging
from ...secrets import SecretServiceKeyStore
from ..protocol import PlatformBundle, PlatformId
from .apps import MacAppLauncher
from .audio import MacAudioRecorder
from .browser import MacBrowserLauncher
from .delivery import MacClipboardDelivery
from .feedback import MacFeedback
from .hotkeys import HotkeyService
from .input import MacInputSynth
from .screen import MacScreenCapture
from .system import MacSystemControl
from .target import MacTargetProbe
from .window import MacWindowControl


def build_macos(settings: Settings | None = None) -> PlatformBundle:
    settings = settings or Settings.from_home()
    settings.prepare()
    os.environ.setdefault("VAANI_AMPLITUDE_PATH", str(settings.amplitude_path))
    os.environ.setdefault("VAANI_INDICATOR_CONTROL", str(settings.indicator_control_path))
    target = MacTargetProbe()
    delivery = MacClipboardDelivery(target=target)
    feedback = MacFeedback(
        amplitude_path=settings.amplitude_path,
        control_path=settings.indicator_control_path,
        log_dir=settings.log_dir,
    )
    apps = MacAppLauncher()
    browser = MacBrowserLauncher()
    recorder = MacAudioRecorder(
        settings.audio_dir, amplitude_path=settings.amplitude_path
    )
    hotkeys = HotkeyService(lambda _mode: None)
    system = MacSystemControl()
    window = MacWindowControl()
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
        system=system,
        window=window,
        input=MacInputSynth(),
        screen=MacScreenCapture(),
    )


def run_macos(settings: Settings) -> int:
    sweep_audio_directory(settings.audio_dir)
    os.environ["VAANI_AMPLITUDE_PATH"] = str(settings.amplitude_path)
    os.environ["VAANI_INDICATOR_CONTROL"] = str(settings.indicator_control_path)
    logger = configure_logging(settings.log_dir, debug=settings.debug)

    # Reap orphan pills from a previous crash; visual-only, safe to kill.
    try:
        subprocess.run(
            ["pkill", "-f", r"python -m vaani\.platform\.macos\.indicator_app"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass

    target = MacTargetProbe()
    delivery = MacClipboardDelivery(target=target)
    system = MacSystemControl()
    window = MacWindowControl()
    bundle = PlatformBundle(
        id=PlatformId.MACOS,
        settings=settings,
        recorder=MacAudioRecorder(
            settings.audio_dir, amplitude_path=settings.amplitude_path
        ),
        hotkeys=HotkeyService(lambda _mode: None),
        target=target,
        delivery=delivery,
        apps=MacAppLauncher(),
        browser=MacBrowserLauncher(),
        feedback=MacFeedback(
            amplitude_path=settings.amplitude_path,
            control_path=settings.indicator_control_path,
            log_dir=settings.log_dir,
        ),
        key_store=SecretServiceKeyStore(),
        run=lambda _controller: 0,
        system=system,
        window=window,
        input=MacInputSynth(),
        screen=MacScreenCapture(),
    )
    assembly = assemble(bundle, delivery=delivery, target=target, logger=logger)
    controller = assembly.controller
    logger = assembly.logger

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

    hotkeys = HotkeyService(
        on_hotkey_press,
        on_release=on_hotkey_release,
        on_cancel=controller.cancel,
        on_approve=controller.approve_pending,
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
        controller._sync_policy_hotkeys()
        logger.info(
            "startup complete; hold Option+Space to dictate "
            "(release to stop); Option+Shift+Space literal; "
            "Control+Option+Space assistant; "
            "Esc/Enter only while recording or confirm"
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
