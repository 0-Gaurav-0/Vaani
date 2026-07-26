import os
import sys

from .config import Settings
from .platform import UnsupportedPlatform, build_platform, detect_os
from .secrets import load_env_file


def manual_record() -> int:
    from .groq import GroqClient
    from .secrets import SecretServiceKeyStore, effective_key

    load_env_file()
    settings = Settings.from_home()
    settings.prepare()
    try:
        bundle = build_platform(settings)
    except UnsupportedPlatform as exc:
        print(str(exc), file=sys.stderr)
        return 2
    recorder = bundle.recorder
    store = bundle.key_store
    key = effective_key(store).value
    if not key:
        print("No GROQ_API_KEY configured", file=sys.stderr)
        return 2
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
        recorder.cleanup()
        return 130
    except Exception as exc:
        try:
            recorder.cleanup()
        except Exception:
            pass
        print(f"Recording failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        groq.close()


def main() -> int:
    load_env_file()
    if "--record" in sys.argv[1:]:
        return manual_record()
    debug = "--debug" in sys.argv[1:] or os.environ.get("VAANI_DEBUG", "").strip() in {"1", "true", "yes"}
    settings = Settings.from_home(debug=debug)
    settings.prepare()
    try:
        detect_os()
        bundle = build_platform(settings)
    except UnsupportedPlatform as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return int(bundle.run(None))


if __name__ == "__main__":
    raise SystemExit(main())
