"""T2.4 pack: ports, processes, trash, wifi (SYS-PORT/PROC/DISK/NET)."""
from __future__ import annotations

import os
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from vaani.intent.grammar import Pattern, SlotRule
from vaani.intent.schema import (
    Context,
    Intent,
    PendingAction,
    Result,
    RiskClass,
    SlotSpec,
    Status,
    Support,
    UndoToken,
    Verb,
)
from vaani.platform.protocol import PlatformId, ProcInfo
from vaani.verbs.registry import Registry

PROCS_VERB_NAMES: frozenset[str] = frozenset(
    {
        "system.port.free",
        "system.proc.kill",
        "system.proc.top",
        "system.trash.empty",
        "system.wifi.set",
    }
)

_ALL_SUPPORT = {
    PlatformId.LINUX: Support.SUPPORTED,
    PlatformId.MACOS: Support.SUPPORTED,
    PlatformId.WINDOWS: Support.SUPPORTED,
}

_WIFI_SUPPORT = {
    PlatformId.LINUX: Support.SUPPORTED,
    PlatformId.MACOS: Support.SUPPORTED,
    PlatformId.WINDOWS: Support.DEGRADED,
}

_PROC_TOP_SUPPORT = {
    PlatformId.LINUX: Support.SUPPORTED,
    PlatformId.MACOS: Support.SUPPORTED,
    PlatformId.WINDOWS: Support.SUPPORTED,
}

_PENDING_TTL_S = 20.0
_MAX_KILL_WITHOUT_FORCE = 5

SystemGetter = Callable[[], Any | None]
PlatformGetter = Callable[[], PlatformId]
UidGetter = Callable[[], int | None]


def procs_patterns() -> tuple[Pattern, ...]:
    """Grammar rows for SYS-PORT/PROC/DISK/NET act utterances."""
    return (
        Pattern(
            verb="system.port.free",
            any_of=(("free port", "kill whatever is on port", "kill the thing on port"),),
            slots=(SlotRule(name="port", regex=r"port\s+(\d+)"),),
            priority=50,
            fixed_slots={"signal": "term"},
        ),
        Pattern(
            verb="system.port.free",
            any_of=(("free",), ("port",)),
            slots=(SlotRule(name="port", regex=r"port\s+(\d+)"),),
            exclude=("localhost", "local host", "forward"),
            priority=48,
            fixed_slots={"signal": "term"},
        ),
        Pattern(
            verb="system.proc.kill",
            any_of=(("kill the process named", "kill process named", "kill the process"),),
            slots=(
                SlotRule(
                    name="name",
                    regex=r"(?:kill(?:\s+the)?\s+process(?:\s+named)?)\s+(.+)",
                ),
            ),
            priority=52,
        ),
        Pattern(
            verb="system.proc.top",
            any_of=(
                (
                    "show what's using the most cpu",
                    "show what is using the most cpu",
                    "open activity monitor",
                    "open task manager",
                    "open system monitor",
                ),
            ),
            exact=True,
            priority=45,
        ),
        Pattern(
            verb="system.trash.empty",
            any_of=(
                (
                    "empty the trash",
                    "empty trash",
                    "empty the recycle bin",
                    "empty recycle bin",
                ),
            ),
            exact=True,
            priority=45,
        ),
        Pattern(
            verb="system.wifi.set",
            any_of=(
                (
                    "turn off wi-fi",
                    "turn off wifi",
                    "disable wi-fi",
                    "disable wifi",
                    "wifi off",
                    "wi-fi off",
                ),
            ),
            exact=True,
            priority=45,
            fixed_slots={"enabled": False},
        ),
        Pattern(
            verb="system.wifi.set",
            any_of=(
                (
                    "turn on wi-fi",
                    "turn on wifi",
                    "enable wi-fi",
                    "enable wifi",
                    "wifi on",
                    "wi-fi on",
                ),
            ),
            exact=True,
            priority=45,
            fixed_slots={"enabled": True},
        ),
    )


def _missing_system(action: str, *, rung: int) -> Result:
    return Result(
        status=Status.UNSUPPORTED,
        summary=f"{action} unavailable",
        detail="SystemControl is not available on this platform bundle",
        rung=rung,
    )


def _parse_port(raw: Any) -> int | None:
    try:
        port = int(raw)
    except (TypeError, ValueError):
        return None
    if 1 <= port <= 65535:
        return port
    return None


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).casefold() in {"1", "true", "yes", "on"}


def _has_mod(intent: Intent, name: str) -> bool:
    return name in intent.modifiers or _truthy(intent.slots.get(name))


def _confirmed(intent: Intent) -> bool:
    return _has_mod(intent, "confirmed") or _has_mod(intent, "yes")


def _forced(intent: Intent) -> bool:
    return _has_mod(intent, "force")


def _pending(
    *,
    verb: str,
    slots: Mapping[str, Any],
    materialized: tuple[str, ...],
    risk: RiskClass,
) -> PendingAction:
    return PendingAction(
        id=uuid.uuid4().hex[:12],
        verb=verb,
        slots=dict(slots),
        materialized=materialized,
        risk=risk,
        expires_at=time.time() + _PENDING_TTL_S,
    )


def _format_holders(holders: Sequence[ProcInfo]) -> str:
    parts = [f"{h.name} (PID {h.pid})" for h in holders]
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts)


def _port_materialized(holders: Sequence[ProcInfo], signal: str) -> tuple[str, ...]:
    sig = "KILL" if signal in {"kill", "sigkill", "9"} else "TERM"
    if not holders:
        return ("port.free", "already-free")
    return ("kill", f"-{sig}", *[str(h.pid) for h in holders])


def _display(parts: Sequence[str]) -> str:
    """Human-readable argv display — never fed to a shell."""
    return " ".join(str(p) for p in parts)


def build_procs_verbs(
    *,
    get_system: SystemGetter | None = None,
    get_platform: PlatformGetter | None = None,
    get_uid: UidGetter | None = None,
) -> tuple[Verb, ...]:
    """Build T2.4 verbs with injectable SystemControl seam."""

    platform_of = get_platform or (lambda: PlatformId.LINUX)
    uid_of = get_uid or (lambda: getattr(os, "getuid", lambda: None)())

    def handle_port_free(intent: Intent, context: Context) -> Result:
        system = get_system() if get_system is not None else None
        if system is None:
            return _missing_system("Port free", rung=2)
        port = _parse_port(intent.slots.get("port"))
        if port is None:
            return Result(
                status=Status.FAILED,
                summary="Invalid port",
                detail="system.port.free requires port in 1..65535",
                rung=2,
            )
        signal = str(intent.slots.get("signal") or "term").casefold()
        if signal not in {"term", "kill", "sigterm", "sigkill", "9", "15"}:
            signal = "term"

        holders = tuple(system.list_listeners(port))
        if "dry_run" in intent.modifiers:
            materialized = _port_materialized(holders, signal)
            shown = _display(materialized)
            return Result(
                status=Status.DRY_RUN,
                summary=shown[:80],
                detail=shown,
                evidence=materialized,
                rung=2,
            )

        if not holders:
            return Result(
                status=Status.OK,
                summary=f"Port {port} is already free",
                detail=f"No LISTEN holder on port {port}",
                evidence=(f"port={port}",),
                rung=2,
            )

        me = uid_of()
        foreign = [
            h for h in holders if h.uid is not None and me is not None and h.uid != me
        ]
        if foreign:
            detail = _format_holders(foreign)
            return Result(
                status=Status.REFUSED,
                summary="Port held by another user",
                detail=f"Refusing to kill {detail} (not owned by current user)",
                evidence=tuple(str(h.pid) for h in foreign),
                rung=2,
            )

        materialized = _port_materialized(holders, signal)
        summary = f"Kill {_format_holders(holders)} on port {port}?"
        if not _confirmed(intent):
            return Result(
                status=Status.NEEDS_CONFIRM,
                summary=summary[:80],
                detail=summary,
                evidence=materialized,
                rung=2,
                pending=_pending(
                    verb="system.port.free",
                    slots={"port": port, "signal": signal},
                    materialized=materialized,
                    risk=RiskClass.R2,
                ),
            )

        result = system.kill_pids([h.pid for h in holders], signal=signal)
        if result.status is Status.OK:
            return Result(
                status=Status.OK,
                summary=f"Freed port {port}",
                detail=f"Signaled {_format_holders(holders)}",
                evidence=materialized,
                rung=2,
            )
        return Result(
            status=result.status,
            summary=result.summary or f"Could not free port {port}",
            detail=result.detail,
            evidence=materialized,
            rung=2,
        )

    def handle_proc_kill(intent: Intent, _context: Context) -> Result:
        system = get_system() if get_system is not None else None
        if system is None:
            return _missing_system("Process kill", rung=2)
        name = str(intent.slots.get("name") or "").strip()
        if not name:
            return Result(
                status=Status.FAILED,
                summary="No process name",
                detail="system.proc.kill requires a name slot",
                rung=2,
            )

        matches = tuple(system.list_named(name))
        if "dry_run" in intent.modifiers:
            materialized = ("pkill", "-f", name)
            return Result(
                status=Status.DRY_RUN,
                summary=_display(materialized)[:80],
                detail=f"{len(matches)} match(es): {_format_holders(matches)}"
                if matches
                else "no matches",
                evidence=materialized,
                rung=2,
            )

        if not matches:
            return Result(
                status=Status.FAILED,
                summary=f"No process named {name}",
                detail="list_named returned no matches",
                evidence=(name,),
                rung=2,
            )

        if len(matches) > _MAX_KILL_WITHOUT_FORCE and not _forced(intent):
            listed = _format_holders(matches)
            return Result(
                status=Status.REFUSED,
                summary=f"Too many matches ({len(matches)})",
                detail=(
                    f"Found {len(matches)} processes matching {name!r}: {listed}. "
                    "Pass force to allow killing more than 5."
                ),
                evidence=tuple(str(m.pid) for m in matches),
                rung=2,
            )

        listed = _format_holders(matches)
        summary = f"Kill {len(matches)} process(es): {listed}?"
        materialized = ("kill", "-TERM", *[str(m.pid) for m in matches])
        if not _confirmed(intent):
            return Result(
                status=Status.NEEDS_CONFIRM,
                summary=summary[:80],
                detail=summary,
                evidence=materialized,
                rung=2,
                pending=_pending(
                    verb="system.proc.kill",
                    slots={"name": name, "force": _forced(intent)},
                    materialized=materialized,
                    risk=RiskClass.R2,
                ),
            )

        result = system.kill_pids([m.pid for m in matches], signal="term")
        if result.status is Status.OK:
            return Result(
                status=Status.OK,
                summary=f"Killed {len(matches)} process(es)",
                detail=listed,
                evidence=materialized,
                rung=2,
            )
        return Result(
            status=result.status,
            summary=result.summary or "Could not kill process",
            detail=result.detail,
            evidence=materialized,
            rung=2,
        )

    def handle_proc_top(_intent: Intent, _context: Context) -> Result:
        system = get_system() if get_system is not None else None
        if system is None:
            return _missing_system("Process monitor", rung=1)
        if "dry_run" in _intent.modifiers:
            plat = _context.platform if _context.platform else platform_of()
            if plat is PlatformId.MACOS:
                materialized = ("open", "-a", "Activity Monitor")
            elif plat is PlatformId.WINDOWS:
                materialized = ("taskmgr.exe",)
            else:
                materialized = ("gnome-system-monitor",)
            shown = _display(materialized)
            return Result(
                status=Status.DRY_RUN,
                summary=shown[:80],
                detail=shown,
                evidence=materialized,
                rung=1,
            )
        return system.open_process_monitor()

    def handle_trash_empty(intent: Intent, _context: Context) -> Result:
        system = get_system() if get_system is not None else None
        if system is None:
            return _missing_system("Empty trash", rung=1)
        materialized = ("trash.empty",)
        if "dry_run" in intent.modifiers:
            return Result(
                status=Status.DRY_RUN,
                summary="Empty the trash",
                detail="system.trash.empty",
                evidence=materialized,
                rung=1,
            )
        if not _confirmed(intent):
            summary = "Empty the trash?"
            return Result(
                status=Status.NEEDS_CONFIRM,
                summary=summary,
                detail=summary,
                evidence=materialized,
                rung=1,
                pending=_pending(
                    verb="system.trash.empty",
                    slots={},
                    materialized=materialized,
                    risk=RiskClass.R2,
                ),
            )
        return system.trash_empty()

    def handle_wifi_set(intent: Intent, _context: Context) -> Result:
        system = get_system() if get_system is not None else None
        if system is None:
            return _missing_system("Wi-Fi", rung=2)
        if "enabled" not in intent.slots:
            return Result(
                status=Status.FAILED,
                summary="No Wi-Fi state",
                detail="system.wifi.set requires enabled=true|false",
                rung=2,
            )
        enabled = _truthy(intent.slots.get("enabled"))
        action = "on" if enabled else "off"
        materialized = ("wifi", action)
        if "dry_run" in intent.modifiers:
            return Result(
                status=Status.DRY_RUN,
                summary=f"Turn Wi-Fi {action}",
                detail=f"system.wifi.set enabled={enabled}",
                evidence=materialized,
                rung=2,
            )
        if not _confirmed(intent):
            summary = f"Turn Wi-Fi {action}?"
            return Result(
                status=Status.NEEDS_CONFIRM,
                summary=summary,
                detail=summary,
                evidence=materialized,
                rung=2,
                pending=_pending(
                    verb="system.wifi.set",
                    slots={"enabled": enabled},
                    materialized=materialized,
                    risk=RiskClass.R2,
                ),
            )
        result = system.wifi(enabled)
        if result.status is Status.OK:
            undo = UndoToken(
                verb="system.wifi.set",
                inverse_verb="system.wifi.set",
                slots={"enabled": not enabled},
                expires_at=time.time() + 60.0,
            )
            return Result(
                status=Status.OK,
                summary=result.summary,
                detail=result.detail,
                evidence=result.evidence or materialized,
                rung=2,
                undo=undo,
            )
        return result

    # Attach dry_run callables for CLI (cli prefers handler.dry_run).
    handle_port_free.dry_run = (  # type: ignore[attr-defined]
        lambda intent, context: handle_port_free(
            Intent(
                verb=intent.verb,
                slots=intent.slots,
                rung=intent.rung,
                confidence=intent.confidence,
                source=intent.source,
                mode=intent.mode,
                utterance=intent.utterance,
                raw_utterance=intent.raw_utterance,
                modifiers=frozenset(set(intent.modifiers) | {"dry_run"}),
                brain=intent.brain,
            ),
            context,
        )
    )
    handle_proc_kill.dry_run = (  # type: ignore[attr-defined]
        lambda intent, context: handle_proc_kill(
            Intent(
                verb=intent.verb,
                slots=intent.slots,
                rung=intent.rung,
                confidence=intent.confidence,
                source=intent.source,
                mode=intent.mode,
                utterance=intent.utterance,
                raw_utterance=intent.raw_utterance,
                modifiers=frozenset(set(intent.modifiers) | {"dry_run"}),
                brain=intent.brain,
            ),
            context,
        )
    )
    handle_proc_top.dry_run = (  # type: ignore[attr-defined]
        lambda intent, context: handle_proc_top(
            Intent(
                verb=intent.verb,
                slots=intent.slots,
                rung=intent.rung,
                confidence=intent.confidence,
                source=intent.source,
                mode=intent.mode,
                utterance=intent.utterance,
                raw_utterance=intent.raw_utterance,
                modifiers=frozenset(set(intent.modifiers) | {"dry_run"}),
                brain=intent.brain,
            ),
            context,
        )
    )
    handle_trash_empty.dry_run = (  # type: ignore[attr-defined]
        lambda intent, context: handle_trash_empty(
            Intent(
                verb=intent.verb,
                slots=intent.slots,
                rung=intent.rung,
                confidence=intent.confidence,
                source=intent.source,
                mode=intent.mode,
                utterance=intent.utterance,
                raw_utterance=intent.raw_utterance,
                modifiers=frozenset(set(intent.modifiers) | {"dry_run"}),
                brain=intent.brain,
            ),
            context,
        )
    )
    handle_wifi_set.dry_run = (  # type: ignore[attr-defined]
        lambda intent, context: handle_wifi_set(
            Intent(
                verb=intent.verb,
                slots=intent.slots,
                rung=intent.rung,
                confidence=intent.confidence,
                source=intent.source,
                mode=intent.mode,
                utterance=intent.utterance,
                raw_utterance=intent.raw_utterance,
                modifiers=frozenset(set(intent.modifiers) | {"dry_run"}),
                brain=intent.brain,
            ),
            context,
        )
    )

    return (
        Verb(
            name="system.port.free",
            title="Free a TCP port",
            slots={
                "port": SlotSpec(type="int", required=True),
                "signal": SlotSpec(type="str", required=False, default="term"),
            },
            rung=2,
            risk=RiskClass.R2,
            requires=frozenset(),
            support=_ALL_SUPPORT,
            undo=None,
            pack="procs",
            handler=handle_port_free,
        ),
        Verb(
            name="system.proc.kill",
            title="Kill processes by name",
            slots={
                "name": SlotSpec(type="str", required=True),
                "force": SlotSpec(type="bool", required=False, default=False),
            },
            rung=2,
            risk=RiskClass.R2,
            requires=frozenset(),
            support=_ALL_SUPPORT,
            undo=None,
            pack="procs",
            handler=handle_proc_kill,
        ),
        Verb(
            name="system.proc.top",
            title="Open the process monitor",
            slots={},
            rung=1,
            risk=RiskClass.R0,
            requires=frozenset(),
            support=_PROC_TOP_SUPPORT,
            undo=None,
            pack="procs",
            handler=handle_proc_top,
        ),
        Verb(
            name="system.trash.empty",
            title="Empty the trash",
            slots={},
            rung=1,
            risk=RiskClass.R2,
            requires=frozenset(),
            support=_ALL_SUPPORT,
            undo=None,
            pack="procs",
            handler=handle_trash_empty,
        ),
        Verb(
            name="system.wifi.set",
            title="Turn Wi-Fi on or off",
            slots={"enabled": SlotSpec(type="bool", required=True)},
            rung=2,
            risk=RiskClass.R2,
            requires=frozenset(),
            support=_WIFI_SUPPORT,
            undo="system.wifi.set",
            pack="procs",
            handler=handle_wifi_set,
        ),
    )


def register_procs_pack(
    registry: Registry,
    *,
    get_system: SystemGetter | None = None,
    get_platform: PlatformGetter | None = None,
    get_uid: UidGetter | None = None,
) -> tuple[Pattern, ...]:
    """Register T2.4 verbs onto an existing registry; return grammar patterns."""
    for verb in build_procs_verbs(
        get_system=get_system,
        get_platform=get_platform,
        get_uid=get_uid,
    ):
        registry.register(verb)
    return procs_patterns()
