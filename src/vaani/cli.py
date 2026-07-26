"""CLI surface: daemon default, record/debug aliases, ``do`` / ``caps`` / ``bridge``."""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Mapping, Sequence

from vaani.apps import launch_app as linux_launch_app
from vaani.apps import resolve_app as linux_resolve_app
from vaani.config import Settings
from vaani.context import NullFocusProbe, build_context
from vaani.exec.runner import run as exec_run
from vaani.exec.supervisor import Supervisor
from vaani.intent.schema import Context, Intent, Result, Status, Support
from vaani.platform import UnsupportedPlatform, build_platform, detect_os
from vaani.platform.protocol import PlatformId
from vaani.policy.dryrun import dispatch, materialize_argv
from vaani.policy.undo import UndoStack, register_undo
from vaani.secrets import load_env_file
from vaani.sites import resolve_site
from vaani.verbs.packs.core import build_core_registry
from vaani.verbs.packs.registry import PackRegistry, register_stub_packs
from vaani.verbs.registry import Registry

# Re-export for callers/tests that imported materialize from the CLI module.
__all__ = (
    "build_parser",
    "build_registry",
    "cmd_bridge",
    "cmd_caps",
    "cmd_do",
    "main",
    "manual_record",
    "materialize_argv",
    "run_daemon",
)

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

    bridge_p = sub.add_parser(
        "bridge",
        help="localhost control bridge (POST /v1/intent; bearer auth)",
    )
    bridge_p.add_argument(
        "--host",
        default="127.0.0.1",
        help="bind address (loopback only; default 127.0.0.1)",
    )
    bridge_p.add_argument(
        "--port",
        type=int,
        default=32123,
        help="bind port (default 32123)",
    )
    bridge_p.add_argument(
        "--token",
        default=None,
        help="Bearer token (or set VAANI_BRIDGE_TOKEN)",
    )

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


def _window_for(platform: PlatformId) -> Any:
    if platform is PlatformId.MACOS:
        from vaani.platform.macos.window import MacWindowControl

        return MacWindowControl()
    if platform is PlatformId.WINDOWS:
        from vaani.platform.windows.window import WindowsWindowControl

        return WindowsWindowControl()
    from vaani.platform.linux.window import LinuxWindowControl

    return LinuxWindowControl()


def _input_for(platform: PlatformId) -> Any:
    if platform is PlatformId.MACOS:
        from vaani.platform.macos.input import MacInputSynth

        return MacInputSynth()
    if platform is PlatformId.WINDOWS:
        from vaani.platform.windows.input import WindowsInputSynth

        return WindowsInputSynth()
    from vaani.platform.linux.input import LinuxInputSynth

    return LinuxInputSynth()


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


def _packs_for_settings(settings: Settings | None = None) -> PackRegistry:
    cfg = settings if settings is not None else Settings.from_home()
    try:
        cfg.prepare()
    except Exception:
        pass
    return PackRegistry(cfg.packs_path)


def build_registry(
    platform: PlatformId | None = None,
    *,
    packs: PackRegistry | None = None,
) -> Registry:
    plat = platform if platform is not None else detect_os()
    resolve_app_fn, launch_app_fn = _resolve_launch(plat)
    open_browser_fn = _open_browser_for(plat)
    system = _system_for(plat)
    window = _window_for(plat)
    delivery = _delivery_for(plat)
    supervisor: Supervisor | None = None
    settings: Settings | None = None
    try:
        settings = Settings.from_home()
        settings.prepare()
        supervisor = Supervisor(settings)
        supervisor.adopt_or_clear()
    except Exception:
        supervisor = None
    registry, _patterns = build_core_registry(
        resolve_app_fn=resolve_app_fn,
        launch_app_fn=launch_app_fn,
        resolve_site_fn=resolve_site,
        open_browser_fn=open_browser_fn,
        get_system=lambda: system,
        get_window=lambda: window,
        get_delivery=lambda: delivery,
        get_platform=lambda: plat,
        get_supervisor=lambda: supervisor,
        run_command=exec_run,
    )
    register_undo(registry, UndoStack())
    input_synth = _input_for(plat)
    register_stub_packs(
        registry,
        run_fn=exec_run,
        get_platform=lambda: plat,
        get_input=lambda: input_synth,
    )
    pack_reg = packs if packs is not None else _packs_for_settings(settings)
    pack_reg.apply(registry)
    return registry


def _make_context(platform: PlatformId) -> Context:
    """Resolve workspace/repo/focus for ``vaani do`` (best-effort).

    Headless CLI skips live OS focus probes by default so CI stays deterministic;
    ``VAANI_ASSISTANT_CWD`` / last-used / home still apply. Set
    ``VAANI_CLI_FOCUS=1`` to enable the platform focus probe.
    """
    use_focus = os.environ.get("VAANI_CLI_FOCUS", "").strip() in {"1", "true", "yes"}
    if use_focus:
        return build_context(platform, runner=exec_run)
    return build_context(
        platform,
        focus_probe=NullFocusProbe(),
        include_focus=True,
        runner=exec_run,
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
        "workspace": str(result.workspace) if result.workspace is not None else None,
        "workspace_source": result.workspace_source,
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
    # Coerce common typed slots from CLI strings.
    if "port" in slots:
        try:
            slots["port"] = int(slots["port"])
        except (TypeError, ValueError):
            pass
    if "enabled" in slots:
        slots["enabled"] = str(slots["enabled"]).casefold() in {
            "1",
            "true",
            "yes",
            "on",
        }
    if "force" in slots:
        slots["force"] = str(slots["force"]).casefold() in {
            "1",
            "true",
            "yes",
            "on",
        }
    # Handlers that resolve from utterance expect natural phrasing for app.open.
    if verb_name == "app.open" and "name" in slots:
        name = str(slots["name"])
        utterance = f"open {name}"
    else:
        utterance = " ".join(
            [verb_name, *[f"{k}={slots[k]}" for k in sorted(slots)]]
        )
    mods: set[str] = set()
    if dry_run:
        mods.add("dry_run")
    if yes:
        # Until T2.1 pill confirm lands, --yes stands in for Approve.
        mods.add("confirmed")
    intent = Intent(
        verb=verb_name,
        slots=slots,
        rung=verb.rung,
        confidence=1.0,
        source="cli",
        mode="act",
        utterance=utterance,
        raw_utterance=utterance,
        modifiers=frozenset(mods),
        brain=None,
    )
    context = _make_context(plat)
    result = dispatch(verb, intent, context)
    argv = list(result.evidence) if result.status is Status.DRY_RUN else None
    _print_result(result, as_json=as_json, verb=verb_name, slots=slots, argv=argv)
    if result.status is Status.FAILED:
        return 1
    if result.status is Status.UNSUPPORTED:
        return 2
    return 0


def _verbs_matrix_json(registry: Registry) -> dict[str, Any]:
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


def _caps_json(registry: Registry, packs: PackRegistry) -> dict[str, Any]:
    return {
        "packs": packs.caps_payload(),
        "verbs": _verbs_matrix_json(registry),
    }


def cmd_bridge(
    *,
    host: str = "127.0.0.1",
    port: int = 32123,
    token: str | None = None,
) -> int:
    """Start the localhost control bridge (T8.2 / P3-01, P3-02)."""
    from vaani.remote.bridge import BindError, serve_bridge

    load_env_file()
    try:
        return serve_bridge(host=host, port=port, token=token)
    except BindError as exc:
        print(str(exc), file=sys.stderr)
        return 2


def cmd_caps(
    *,
    as_json: bool = False,
    registry: Registry | None = None,
    packs: PackRegistry | None = None,
) -> int:
    pack_reg = packs if packs is not None else _packs_for_settings()
    reg = registry if registry is not None else build_registry(packs=pack_reg)
    if registry is not None:
        # Ensure injected registries still reflect pack state for caps.
        pack_reg.apply(reg)
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
        print(json.dumps(_caps_json(reg, pack_reg), sort_keys=True, separators=(",", ":")))
        return 0

    print("packs")
    print("-----")
    for name, row in pack_reg.caps_payload().items():
        note = row.get("note") or ""
        suffix = f"  ({note})" if note else ""
        print(f"{name:<16}{row['support']:<12}enabled={row['enabled']}{suffix}")
    print()

    platforms = [p.value for p in _PLATFORMS]
    verb_width = max((len(name) for name in matrix), default=24)
    verb_width = max(verb_width, 24) + 2
    header = f"{'verb':<{verb_width}}" + "".join(f"{p:<12}" for p in platforms)
    print(header)
    print("-" * len(header))
    for verb_name in sorted(matrix):
        cells = []
        for platform in _PLATFORMS:
            support, _note = matrix[verb_name][platform.value]
            cells.append(f"{support.value:<12}")
        print(f"{verb_name:<{verb_width}}" + "".join(cells))
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
    if args.command == "bridge":
        return cmd_bridge(
            host=str(args.host),
            port=int(args.port),
            token=args.token,
        )
    if args.command == "debug":
        return run_daemon(debug=True)
    return run_daemon(debug=debug)


if __name__ == "__main__":
    raise SystemExit(main())
