"""Assemble and run the Linux/X11 PlatformBundle."""
from __future__ import annotations

import os
import select
import signal
import subprocess
import sys

from ...audio import AudioRecorderImpl, reap_orphan_parec
from ...codex import CodexRunner, ResultWindow
from ...config import Settings, sweep_audio_directory
from ...controller import Controller
from ...delivery import ClipboardDelivery
from ...groq import GroqClient
from ...history import HistoryStore
from ...hotkeys import MiddleButtonHotkeyManager, XTestMediaKeySender
from ...observability import configure_logging
from ...secrets import SecretServiceKeyStore, effective_key
from ...gemini_stt import gemini_api_key
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


def _reap_orphan_mic() -> None:
    """Release the microphone if a prior Vaani crash left ``parec`` running."""
    try:
        killed = reap_orphan_parec()
        if killed:
            print(f"[vaani] released mic: reaped {killed} orphan recorder(s)", flush=True)
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


def _detect_display_backend(env: dict | None = None) -> str:
    """Pick the Linux backend: Wayland if the session is Wayland, else X11.

    ``WAYLAND_DISPLAY`` is the primary signal (set by the compositor for any
    Wayland session, GNOME/KDE/Sway alike); ``XDG_SESSION_TYPE=wayland`` is
    the fallback for the rare case a Wayland compositor doesn't set the
    former. Anything else (including XWayland-only setups, which still
    export ``DISPLAY``) defaults to the existing X11 backend.
    """
    env = env if env is not None else os.environ
    if env.get("WAYLAND_DISPLAY"):
        return "wayland"
    if env.get("XDG_SESSION_TYPE", "").lower() == "wayland":
        return "wayland"
    return "x11"


def run_linux(settings: Settings) -> int:
    if _detect_display_backend() == "wayland":
        from .wayland.runtime import run_wayland

        return run_wayland(settings)
    return _run_x11(settings)


def _run_x11(settings: Settings) -> int:
    _reap_orphan_indicators()
    _reap_orphan_mic()
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
            "[vaani] X11 display unavailable — no X server reachable. "
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
        # Actions/results use the pill or browser itself — no notify-send spam.
        logger.info("event=assistant_result_ui skipped=notify chars=%s", len(text or ""))

    controller.codex = assistant
    controller.result_window = ResultWindow(show_assistant_result)

    def on_hotkey_press(mode: str) -> bool:
        # Hold-to-talk: press starts. Ignore repeats while already capturing.
        from ...types import AppState

        if controller.state is AppState.PROCESSING:
            logger.info("event=input_blocked reason=processing source=press")
            return False
        if controller.state is AppState.RECORDING:
            return False
        return bool(controller.trigger(mode))

    def on_hotkey_release(_mode: str) -> None:
        from ...types import AppState

        if controller.state is AppState.PROCESSING:
            return
        # Release stops capture; pill switches to processing animation.
        controller.stop()

    def on_signal_toggle(_signum=None, _frame=None):
        # SIGUSR1 is stop-only. Late pill Stop must never ghost-start when idle.
        # Start dictation via middle-button hold (or file control), not this signal.
        from ...types import AppState

        if controller.state is AppState.RECORDING:
            controller.stop()
        else:
            logger.info(
                "event=signal_usr1_ignored state=%s reason=stop_only",
                getattr(controller.state, "name", controller.state),
            )

    def assistant_trigger(_signum=None, _frame=None):
        on_hotkey_press("assistant")

    media_keys = XTestMediaKeySender()

    def on_touchpad_middle() -> None:
        try:
            media_keys.play_pause()
            logger.info("event=media_play_pause source=touchpad_middle")
        except Exception as exc:
            logger.error(
                "event=media_play_pause_failed detail=%s", type(exc).__name__
            )

    # Middle button owns hold-to-talk on ThinkPads; Ctrl+Space stays with the desktop.
    hotkeys = MiddleButtonHotkeyManager(
        on_hotkey_press,
        on_release=on_hotkey_release,
        on_cancel=controller.cancel,
        on_touchpad_middle=on_touchpad_middle,
    )
    controller.hotkeys = hotkeys
    try:
        log_path = settings.log_dir / "vaani.log"
        session_type = os.environ.get("XDG_SESSION_TYPE", "unknown")
        print(f"[vaani] log file: {log_path}", flush=True)
        print(
            f"[vaani] debug={'on' if settings.debug else 'off'} (use --debug)",
            flush=True,
        )
        print(
            f"[vaani] session={session_type} (middle-button hold-to-talk; X11 used for paste)",
            flush=True,
        )
        logger.info(
            "event=startup_linux log=%s debug=%s session=%s backend=middle-button stt=%s",
            log_path,
            settings.debug,
            session_type,
            "gemini" if gemini_api_key() else "groq",
        )
        hotkeys.register()
        logger.info(
            "startup complete; hold ThinkPad middle button to dictate (release to stop); "
            "double-press+hold for assistant; Esc cancels; Ctrl+Space is not used"
        )
        print(
            "Vaani hotkeys ready (ThinkPad middle button):\n"
            "  Hold middle button              → smart dictation\n"
            "  Double-press + hold middle      → assistant\n"
            "  Esc                             → cancel\n"
            "Release to stop — pill stays up while processing.\n"
            "Ctrl+Space is left for the desktop / IME.\n"
            "Assistant: say “open …” / “play … on YouTube” for fast actions;\n"
            "  say “run the <skill> skill …” for Agent Skills (lazy MCP);\n"
            "  other requests use isolated Codex (up to ~30s).",
            flush=True,
        )
        signal.signal(signal.SIGINT, lambda *_: controller.shutdown())
        signal.signal(signal.SIGTERM, lambda *_: controller.shutdown())
        signal.signal(signal.SIGUSR1, on_signal_toggle)
        signal.signal(signal.SIGUSR2, lambda *_: controller.cancel())
        assistant_signal = getattr(
            signal, "SIGUSR3", getattr(signal, "SIGRTMIN", signal.SIGUSR1 + 2)
        )
        signal.signal(assistant_signal, assistant_trigger)
        while not controller._shutdown:
            # Keep the X11 connection drained for clipboard/focus helpers.
            readable, _, _ = select.select([display.fileno()], [], [], 0.2)
            if readable or display.pending_events():
                while display.pending_events():
                    display.next_event()
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
        try:
            hotkeys.unregister()
        except Exception:
            pass
        try:
            recorder.cleanup()
        except Exception:
            pass
        try:
            _reap_orphan_mic()
        except Exception:
            pass
        try:
            display.close()
        except Exception:
            pass
        logger.info("event=shutdown_linux")
    return 0
