import signal
import sys

from .audio import AudioRecorderImpl
from .config import Settings, sweep_audio_directory
from .controller import Controller
from .delivery import ClipboardDelivery
from .feedback import Feedback
from .groq import GroqClient
from .history import HistoryStore
from .hotkeys import HotkeyManager, XInputHotkeyManager
from .observability import configure_logging
from .secrets import SecretServiceKeyStore, effective_key
from .x11 import X11Probe
from .codex import CodexRunner, ResultWindow

def manual_record() -> int:
    settings = Settings.from_home(); settings.prepare()
    recorder = AudioRecorderImpl(settings.audio_dir)
    store = SecretServiceKeyStore(); key = effective_key(store).value
    if not key:
        print("No GROQ_API_KEY configured", file=sys.stderr); return 2
    groq = GroqClient()
    try:
        recorder.start()
        print("Recording… press Enter to stop.", flush=True)
        input()
        audio = recorder.stop()
        result = groq.transcribe(audio.path, key, delete_audio=True)
        print(result.text)
        return 0
    except KeyboardInterrupt:
        recorder.cleanup(); return 130
    except Exception as exc:
        recorder.cleanup(); print(f"Recording failed: {type(exc).__name__}: {exc}", file=sys.stderr); return 1
    finally:
        groq.close()


def main() -> int:
    if "--record" in sys.argv[1:]:
        return manual_record()
    settings = Settings.from_home()
    settings.prepare()
    # Reap indicators left by a previous crash/restart before starting a new
    # session; the indicator is purely visual and has no work to preserve.
    import subprocess
    subprocess.run(["pkill", "-f", r"python -m vaani\.indicator"], check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    sweep_audio_directory(settings.audio_dir)
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
    controller = Controller(
        recorder=recorder, groq=groq, delivery=delivery, history=history,
        feedback=feedback, key_provider=lambda: effective_key(store).value,
    )
    # Assistant mode is deliberately an additive seam; dictation delivery is unchanged.
    assistant = CodexRunner()
    def show_assistant_result(text: str) -> None:
        import subprocess
        subprocess.run(["notify-send", "Vaani assistant", text[:1800]], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    result_window = ResultWindow(show_assistant_result)
    controller.codex = assistant
    controller.result_window = result_window
    def assistant_trigger(_signum=None, _frame=None):
        controller.handle_hotkey("assistant")
    hotkeys = HotkeyManager(controller.handle_hotkey, display=display)
    controller.hotkeys = hotkeys
    escape_monitor = XInputHotkeyManager(controller.handle_hotkey, on_cancel=controller.cancel)
    try:
        hotkeys.register()
        escape_monitor.register()
        logger.info("startup complete; Ctrl+Space toggles dictation; Ctrl+Shift+Space toggles literal mode")
        signal.signal(signal.SIGINT, lambda *_: controller.shutdown())
        signal.signal(signal.SIGTERM, lambda *_: controller.shutdown())
        signal.signal(signal.SIGUSR1, lambda *_: controller.handle_hotkey("smart"))
        signal.signal(signal.SIGUSR2, lambda *_: controller.cancel())
        assistant_signal = getattr(signal, "SIGUSR3", getattr(signal, "SIGRTMIN", signal.SIGUSR1 + 2))
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
        try: display.close()
        except Exception: pass
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
