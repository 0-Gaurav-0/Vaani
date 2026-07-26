"""CLI surface: daemon default, record/debug aliases, ``do`` / ``caps``."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from typing import Any, Mapping, Sequence

from vaani.apps import APPS as LINUX_APPS
from vaani.apps import launch_app as linux_launch_app
from vaani.apps import resolve_app as linux_resolve_app
from vaani.config import Settings
from vaani.intent.schema import Context, Intent, Result, Status, Support
from vaani.platform import UnsupportedPlatform, build_platform, detect_os
from vaani.platform.protocol import AppTarget, PlatformId
from vaani.secrets import load_env_file
from vaani.sites import resolve_site
from vaani.known_folders import resolve_known_folder
from vaani.verbs.packs.core import build_core_registry
from vaani.verbs.registry import Registry

_PLATFORMS = (PlatformId.LINUX, PlatformId.MACOS, PlatformId.WINDOWS)


def manual_record() -> int:
    from vaani.groq import GroqClient
    from vaani.secrets import SecretServiceKeyStore, effective_key

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


def run_daemon(*, debug: bool = False) -> int:
    load_env_file()
    settings = Settings.from_home(debug=debug)
    settings.prepare()
    try:
        detect_os()
        bundle = build_platform(settings)
    except UnsupportedPlatform as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return int(bundle.run(None))


def _parse_slot(raw: str) -> tuple[str, str]:
    if "=" not in raw:
        raise argparse.ArgumentTypeError(f"slot must be k=v, got {raw!r}")
    key, _, value = raw.partition("=")
    if not key:
        raise argparse.ArgumentTypeError(f"slot key missing in {raw!r}")
    return key, value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vaani", add_help=True)
    parser.add_argument(
        "--record",
        action="store_true",
        help="manual record+transcribe (alias: vaani record)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="enable debug logging for the daemon (alias: vaani debug)",
    )

    sub = parser.add_subparsers(dest="command")

    sub.add_parser("record", help="manual record+transcribe")
    sub.add_parser("debug", help="run daemon with debug logging")

    do_p = sub.add_parser("do", help="run a registered verb")
    do_p.add_argument("verb", help="verb name, e.g. app.open")
    do_p.add_argument(
        "--slot",
        action="append",
        default=[],
        metavar="k=v",
        type=_parse_slot,
        help="slot assignment (repeatable)",
    )
    do_p.add_argument("--json", action="store_true", help="machine-readable output")
    do_p.add_argument(
        "--dry-run",
        action="store_true",
        help="materialize argv without executing the verb handler",
    )
    do_p.add_argument(
        "--yes",
        action="store_true",
        help="auto-approve confirmation when a policy engine is present",
    )

    caps_p = sub.add_parser("caps", help="print verb capability matrix")
    caps_p.add_argument("--json", action="store_true", help="machine-readable output")

    return parser


def _open_browser_for(platform: PlatformId) -> Any:
    if platform is PlatformId.MACOS:
        from vaani.platform.macos.browser import open_browser

        return open_browser
    if platform is PlatformId.WINDOWS:
        from vaani.platform.windows.browser import open_browser

        return open_browser
    from vaani.platform.linux.browser import open_browser

    return open_browser


def _app_catalog(platform: PlatformId) -> tuple[tuple[tuple[str, ...], AppTarget], ...]:
    if platform is PlatformId.MACOS:
        from vaani.platform.macos.apps import APPS

        return APPS
    if platform is PlatformId.WINDOWS:
        from vaani.platform.windows.apps import APPS

        return APPS
    return LINUX_APPS


def _resolve_launch(platform: PlatformId) -> tuple[Any, Any]:
    if platform is PlatformId.MACOS:
        from vaani.platform.macos.apps import launch_app, resolve_app

        return resolve_app, launch_app
    if platform is PlatformId.WINDOWS:
        from vaani.platform.windows.apps import launch_app, resolve_app

        return resolve_app, launch_app
    return linux_resolve_app, linux_launch_app


def _system_for(platform: PlatformId) -> Any:
    if platform is PlatformId.MACOS:
        from vaani.platform.macos.system import MacSystemControl

        return MacSystemControl()
    if platform is PlatformId.WINDOWS:
        from vaani.platform.windows.system import WindowsSystemControl

        return WindowsSystemControl()
    from vaani.platform.linux.system import LinuxSystemControl

    return LinuxSystemControl()


def _delivery_for(platform: PlatformId) -> Any | None:
    """Host delivery when the requested platform matches this machine."""
    try:
        if detect_os() is not platform:
            return None
        settings = Settings.from_home()
        settings.prepare()
        return build_platform(settings).delivery
    except Exception:
        return None


def build_registry(platform: PlatformId | None = None) -> Registry:
    plat = platform if platform is not None else detect_os()
    resolve_app_fn, launch_app_fn = _resolve_launch(plat)
    open_browser_fn = _open_browser_for(plat)
    system = _system_for(plat)
    delivery = _delivery_for(plat)
    registry, _patterns = build_core_registry(
        resolve_app_fn=resolve_app_fn,
        launch_app_fn=launch_app_fn,
        resolve_site_fn=resolve_site,
        open_browser_fn=open_browser_fn,
        get_system=lambda: system,
        get_delivery=lambda: delivery,
        get_platform=lambda: plat,
    )
    return registry


def _find_app_target(
    name: str,
    catalog: tuple[tuple[tuple[str, ...], AppTarget], ...],
) -> AppTarget | None:
    needle = name.casefold().strip()
    for aliases, target in catalog:
        if target.name.casefold() == needle:
            return target
        if any(alias.casefold() == needle for alias in aliases):
            return target
    return None


def materialize_argv(
    verb_name: str,
    slots: Mapping[str, Any],
    *,
    platform: PlatformId,
) -> tuple[str, ...] | None:
    """Best-effort argv for dry-run. Returns None when no shape is known."""
    if verb_name == "app.open":
        name = str(slots.get("name") or "")
        target = _find_app_target(name, _app_catalog(platform))
        if target is None:
            return None
        if platform is PlatformId.MACOS:
            return ("open", "-a", target.native_name or target.name)
        executable = next(
            (path for exe in target.executables if (path := shutil.which(exe))),
            target.executables[0] if target.executables else name,
        )
        return (executable, *target.arguments)

    if verb_name in {"site.open", "browser.open", "site.search"}:
        if verb_name == "site.search":
            from urllib.parse import quote_plus

            query = str(slots.get("query") or "")
            url = f"https://www.google.com/search?q={quote_plus(query)}"
        elif slots.get("port") and not slots.get("url"):
            url = f"http://localhost:{slots['port']}"
        else:
            url = str(slots.get("url") or "about:blank")
        if platform is PlatformId.MACOS:
            return ("open", url)
        if platform is PlatformId.WINDOWS:
            return ("cmd", "/c", "start", "", url)
        return ("xdg-open", url)

    if verb_name == "browser.window.private":
        if platform is PlatformId.MACOS:
            return ("open", "-na", "Brave Browser", "--args", "--incognito")
        if platform is PlatformId.WINDOWS:
            return ("brave.exe", "--incognito")
        return ("brave-browser", "--incognito")

    if verb_name == "app.quit":
        name = str(slots.get("name") or "App")
        if platform is PlatformId.MACOS:
            return ("osascript", "-e", f'tell application "{name}" to quit')
        if platform is PlatformId.WINDOWS:
            return ("taskkill", "/IM", f"{name}.exe")
        return ("wmctrl", "-c", name)

    if verb_name == "system.lock":
        if platform is PlatformId.MACOS:
            return (
                "/System/Library/CoreServices/Menu Extras/User.menu/Contents/Resources/CGSession",
                "-suspend",
            )
        if platform is PlatformId.WINDOWS:
            return ("rundll32", "user32.dll,LockWorkStation")
        return ("loginctl", "lock-session")

    if verb_name == "system.display.sleep":
        if platform is PlatformId.MACOS:
            return ("pmset", "displaysleepnow")
        if platform is PlatformId.WINDOWS:
            return ("powershell", "-Command", "display-sleep")
        return ("xset", "dpms", "force", "off")

    if verb_name == "system.volume.set":
        if "muted" in slots:
            flag = "1" if slots.get("muted") in {True, "true", "1", 1} else "0"
            if platform is PlatformId.MACOS:
                muted = "true" if flag == "1" else "false"
                return ("osascript", "-e", f"set volume output muted {muted}")
            return ("pactl", "set-sink-mute", "@DEFAULT_SINK@", flag)
        level = str(slots.get("level") or "0")
        if platform is PlatformId.MACOS:
            return ("osascript", "-e", f"set volume output volume {level}")
        if platform is PlatformId.WINDOWS:
            return ("volume.set", level)
        return ("pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{level}%")

    if verb_name == "system.dnd.set":
        enabled = slots.get("enabled", True) not in {False, "false", "0", 0}
        if platform is PlatformId.LINUX:
            banners = "false" if enabled else "true"
            return (
                "gsettings",
                "set",
                "org.gnome.desktop.notifications",
                "show-banners",
                banners,
            )
        return ("system.dnd.set", "on" if enabled else "off")

    if verb_name == "system.ip.copy":
        return ("system.ip.copy",)

    if verb_name == "files.open_dir":
        folder = str(slots.get("folder") or slots.get("name") or "")
        path = resolve_known_folder(folder, platform)
        if path is None:
            return ("files.open_dir", folder)
        if platform is PlatformId.MACOS:
            return ("open", str(path))
        if platform is PlatformId.WINDOWS:
            return ("explorer", str(path))
        return ("xdg-open", str(path))

    if verb_name == "files.reveal":
        if platform is PlatformId.MACOS:
            return ("open", "-R", "${workspace}")
        if platform is PlatformId.WINDOWS:
            return ("explorer", "/select,${workspace}")
        return ("nautilus", "--select", "${workspace}")

    if verb_name == "agent.task":
        from vaani.codex import CodexRunner

        prompt = str(slots.get("prompt") or "")
        return tuple(CodexRunner.command_for("codex", prompt))

    return None


def _make_context(platform: PlatformId) -> Context:
    return Context(
        platform=platform,
        workspace=None,
        workspace_source="cli",
        repo=None,
        project=None,
        focus=None,
        screen=None,
        session=None,
    )


def _result_payload(
    result: Result,
    *,
    verb: str,
    slots: Mapping[str, Any],
    argv: Sequence[str] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": result.status.value,
        "verb": verb,
        "slots": {k: slots[k] for k in sorted(slots)},
        "summary": result.summary,
        "detail": result.detail,
        "evidence": list(result.evidence),
        "rung": result.rung,
    }
    if argv is not None:
        payload["argv"] = list(argv)
    return payload


def _print_result(
    result: Result,
    *,
    as_json: bool,
    verb: str,
    slots: Mapping[str, Any],
    argv: Sequence[str] | None = None,
) -> None:
    if as_json:
        print(
            json.dumps(
                _result_payload(result, verb=verb, slots=slots, argv=argv),
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return
    if argv is not None:
        print(" ".join(argv))
        return
    text = result.detail or result.summary
    if text:
        print(text)


def _catalog_names(registry: Registry) -> list[str]:
    return sorted(registry.matrix())


def cmd_do(
    verb_name: str,
    slot_pairs: Sequence[tuple[str, str]],
    *,
    as_json: bool = False,
    dry_run: bool = False,
    yes: bool = False,
    platform: PlatformId | None = None,
    registry: Registry | None = None,
) -> int:
    _ = yes  # confirm policy lands later; accepted for forward compatibility
    plat = platform if platform is not None else detect_os()
    reg = registry if registry is not None else build_registry(plat)
    verb = reg.get(verb_name)
    if verb is None:
        names = _catalog_names(reg)
        message = {
            "error": "unknown_verb",
            "verb": verb_name,
            "catalog": names,
        }
        if as_json:
            print(json.dumps(message, sort_keys=True, separators=(",", ":")))
        else:
            print(f"unknown verb: {verb_name}", file=sys.stderr)
            print("catalog:", ", ".join(names), file=sys.stderr)
        return 2

    slots: dict[str, Any] = {key: value for key, value in slot_pairs}
    # Handlers that resolve from utterance expect natural phrasing for app.open.
    if verb_name == "app.open" and "name" in slots:
        name = str(slots["name"])
        utterance = f"open {name}"
    else:
        utterance = " ".join(
            [verb_name, *[f"{k}={slots[k]}" for k in sorted(slots)]]
        )
    intent = Intent(
        verb=verb_name,
        slots=slots,
        rung=verb.rung,
        confidence=1.0,
        source="cli",
        mode="act",
        utterance=utterance,
        raw_utterance=utterance,
        modifiers=frozenset({"dry_run"} if dry_run else ()),
        brain=None,
    )
    context = _make_context(plat)

    if dry_run:
        dry_handler = getattr(verb.handler, "dry_run", None)
        if callable(dry_handler):
            result = dry_handler(intent, context)
            argv = list(result.evidence) if result.evidence else None
            _print_result(result, as_json=as_json, verb=verb_name, slots=slots, argv=argv)
            return 0 if result.status is not Status.FAILED else 1

        argv = materialize_argv(verb_name, slots, platform=plat)
        if argv is None:
            argv = (verb_name, *[f"{k}={slots[k]}" for k in sorted(slots)])
        result = Result(
            status=Status.DRY_RUN,
            summary=" ".join(argv)[:80],
            detail=" ".join(argv),
            evidence=argv,
            rung=verb.rung,
        )
        _print_result(result, as_json=as_json, verb=verb_name, slots=slots, argv=argv)
        return 0

    result = verb.handler(intent, context)
    _print_result(result, as_json=as_json, verb=verb_name, slots=slots)
    if result.status is Status.FAILED:
        return 1
    if result.status is Status.UNSUPPORTED:
        return 2
    return 0


def _matrix_json(registry: Registry) -> dict[str, Any]:
    matrix = registry.matrix()
    out: dict[str, Any] = {}
    for verb_name in sorted(matrix):
        row: dict[str, Any] = {}
        for platform in _PLATFORMS:
            support, note = matrix[verb_name][platform.value]
            cell: dict[str, str] = {"support": support.value}
            if note:
                cell["note"] = note
            row[platform.value] = cell
        out[verb_name] = row
    return out


def cmd_caps(
    *,
    as_json: bool = False,
    registry: Registry | None = None,
) -> int:
    reg = registry if registry is not None else build_registry()
    matrix = reg.matrix()
    # Completeness: every registered verb has all three platforms filled.
    for verb_name, row in matrix.items():
        for platform in _PLATFORMS:
            if platform.value not in row:
                print(f"blank cell: {verb_name}/{platform.value}", file=sys.stderr)
                return 1
            support, _note = row[platform.value]
            if not isinstance(support, Support):
                print(f"blank cell: {verb_name}/{platform.value}", file=sys.stderr)
                return 1

    if as_json:
        print(json.dumps(_matrix_json(reg), sort_keys=True, separators=(",", ":")))
        return 0

    platforms = [p.value for p in _PLATFORMS]
    header = f"{'verb':<24}" + "".join(f"{p:<12}" for p in platforms)
    print(header)
    print("-" * len(header))
    for verb_name in sorted(matrix):
        cells = []
        for platform in _PLATFORMS:
            support, _note = matrix[verb_name][platform.value]
            cells.append(f"{support.value:<12}")
        print(f"{verb_name:<24}" + "".join(cells))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    debug_env = os.environ.get("VAANI_DEBUG", "").strip().casefold() in {"1", "true", "yes"}
    debug = bool(getattr(args, "debug", False) or args.command == "debug" or debug_env)

    if getattr(args, "record", False) or args.command == "record":
        return manual_record()
    if args.command == "do":
        return cmd_do(
            args.verb,
            list(args.slot or []),
            as_json=bool(args.json),
            dry_run=bool(args.dry_run),
            yes=bool(args.yes),
        )
    if args.command == "caps":
        return cmd_caps(as_json=bool(args.json))
    if args.command == "debug":
        return run_daemon(debug=True)
    return run_daemon(debug=debug)


if __name__ == "__main__":
    raise SystemExit(main())
