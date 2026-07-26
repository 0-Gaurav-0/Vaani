"""Unit tests for T2.4 ports/procs/trash/wifi pack (fakes only)."""
from __future__ import annotations

from vaani.intent.schema import Context, Intent, Result, Status
from vaani.platform.protocol import PlatformId, ProcInfo
from vaani.verbs.packs.procs import PROCS_VERB_NAMES, build_procs_verbs, procs_patterns
from vaani.intent.grammar import match


class _FakeSystem:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.listeners: dict[int, tuple[ProcInfo, ...]] = {}
        self.named: dict[str, tuple[ProcInfo, ...]] = {}
        self.kill_result = Result(status=Status.OK, summary="Signaled", rung=2)
        self.trash_result = Result(status=Status.OK, summary="Trash emptied", rung=1)
        self.wifi_result = Result(status=Status.OK, summary="Wi-Fi off", rung=2)
        self.top_result = Result(status=Status.OK, summary="Opened Activity Monitor", rung=1)

    def list_listeners(self, port: int) -> tuple[ProcInfo, ...]:
        self.calls.append(("list_listeners", port))
        return self.listeners.get(int(port), ())

    def list_named(self, name: str) -> tuple[ProcInfo, ...]:
        self.calls.append(("list_named", name))
        return self.named.get(name, ())

    def kill_pids(self, pids, *, signal: str = "term") -> Result:
        self.calls.append(("kill_pids", (tuple(pids), signal)))
        return self.kill_result

    def trash_empty(self) -> Result:
        self.calls.append(("trash_empty", None))
        return self.trash_result

    def wifi(self, enabled: bool) -> Result:
        self.calls.append(("wifi", enabled))
        return self.wifi_result

    def open_process_monitor(self) -> Result:
        self.calls.append(("open_process_monitor", None))
        return self.top_result


def _intent(
    verb: str,
    slots: dict | None = None,
    *,
    modifiers: frozenset[str] = frozenset(),
) -> Intent:
    return Intent(
        verb=verb,
        slots=slots or {},
        rung=2,
        confidence=1.0,
        source="test",
        mode="act",
        utterance=verb,
        raw_utterance=verb,
        modifiers=modifiers,
        brain=None,
    )


def _context(platform: PlatformId = PlatformId.MACOS) -> Context:
    return Context(
        platform=platform,
        workspace=None,
        workspace_source="test",
        repo=None,
        project=None,
        focus=None,
        screen=None,
        session=None,
    )


def _verbs(system: _FakeSystem, *, uid: int | None = 501):
    return {
        v.name: v
        for v in build_procs_verbs(
            get_system=lambda: system,
            get_platform=lambda: PlatformId.MACOS,
            get_uid=lambda: uid,
        )
    }


def test_procs_verb_names() -> None:
    system = _FakeSystem()
    names = set(_verbs(system))
    assert names == PROCS_VERB_NAMES


def test_port_free_already_free_ok() -> None:
    system = _FakeSystem()
    verbs = _verbs(system)
    result = verbs["system.port.free"].handler(
        _intent("system.port.free", {"port": 34567}),
        _context(),
    )
    assert result.status is Status.OK
    assert "already free" in result.summary.casefold()
    assert not any(c[0] == "kill_pids" for c in system.calls)


def test_port_free_needs_confirm_without_mutating() -> None:
    system = _FakeSystem()
    system.listeners[3000] = (ProcInfo(pid=41233, name="node", uid=501),)
    verbs = _verbs(system, uid=501)
    result = verbs["system.port.free"].handler(
        _intent("system.port.free", {"port": 3000}),
        _context(),
    )
    assert result.status is Status.NEEDS_CONFIRM
    assert result.pending is not None
    assert result.pending.risk.value == "R2"
    assert "41233" in result.detail
    assert "node" in result.detail.casefold()
    assert not any(c[0] == "kill_pids" for c in system.calls)


def test_port_free_confirmed_kills() -> None:
    system = _FakeSystem()
    system.listeners[3000] = (ProcInfo(pid=41233, name="node", uid=501),)
    verbs = _verbs(system, uid=501)
    result = verbs["system.port.free"].handler(
        _intent(
            "system.port.free",
            {"port": 3000},
            modifiers=frozenset({"confirmed"}),
        ),
        _context(),
    )
    assert result.status is Status.OK
    assert "Freed port 3000" in result.summary
    assert ("kill_pids", ((41233,), "term")) in system.calls


def test_port_free_foreign_uid_refused() -> None:
    system = _FakeSystem()
    system.listeners[3000] = (ProcInfo(pid=1, name="rootd", uid=0),)
    verbs = _verbs(system, uid=501)
    result = verbs["system.port.free"].handler(
        _intent("system.port.free", {"port": 3000}),
        _context(),
    )
    assert result.status is Status.REFUSED
    assert not any(c[0] == "kill_pids" for c in system.calls)


def test_port_free_dry_run() -> None:
    system = _FakeSystem()
    system.listeners[8080] = (ProcInfo(pid=9, name="python", uid=501),)
    verbs = _verbs(system)
    result = verbs["system.port.free"].handler(
        _intent(
            "system.port.free",
            {"port": 8080},
            modifiers=frozenset({"dry_run"}),
        ),
        _context(),
    )
    assert result.status is Status.DRY_RUN
    assert not any(c[0] == "kill_pids" for c in system.calls)


def test_proc_kill_multiple_matches_needs_disambiguate() -> None:
    system = _FakeSystem()
    system.named["node"] = (
        ProcInfo(pid=11, name="node"),
        ProcInfo(pid=12, name="node"),
    )
    verbs = _verbs(system)
    result = verbs["system.proc.kill"].handler(
        _intent("system.proc.kill", {"name": "node"}),
        _context(),
    )
    assert result.status is Status.NEEDS_DISAMBIGUATE
    assert result.disambiguation is not None
    assert len(result.disambiguation.options) == 2
    assert "PID 11" in result.detail and "PID 12" in result.detail
    assert not any(c[0] == "kill_pids" for c in system.calls)


def test_proc_kill_disambiguate_truncates_to_three() -> None:
    system = _FakeSystem()
    system.named["node"] = tuple(
        ProcInfo(pid=20 + i, name="node") for i in range(4)
    )
    verbs = _verbs(system)
    result = verbs["system.proc.kill"].handler(
        _intent("system.proc.kill", {"name": "node"}),
        _context(),
    )
    assert result.status is Status.NEEDS_DISAMBIGUATE
    assert result.disambiguation is not None
    assert len(result.disambiguation.options) == 3


def test_proc_kill_after_pid_choice_needs_confirm() -> None:
    system = _FakeSystem()
    system.named["node"] = (
        ProcInfo(pid=11, name="node"),
        ProcInfo(pid=12, name="node"),
    )
    verbs = _verbs(system)
    result = verbs["system.proc.kill"].handler(
        _intent("system.proc.kill", {"name": "node", "pid": 12}),
        _context(),
    )
    assert result.status is Status.NEEDS_CONFIRM
    assert "PID 12" in result.detail
    assert "PID 11" not in result.detail
    assert not any(c[0] == "kill_pids" for c in system.calls)


def test_proc_kill_refuses_more_than_five_without_force() -> None:
    system = _FakeSystem()
    system.named["worker"] = tuple(
        ProcInfo(pid=100 + i, name="worker") for i in range(6)
    )
    verbs = _verbs(system)
    result = verbs["system.proc.kill"].handler(
        _intent("system.proc.kill", {"name": "worker"}),
        _context(),
    )
    assert result.status is Status.REFUSED
    assert "6" in result.summary or "6" in result.detail


def test_proc_kill_force_still_needs_confirm_then_runs() -> None:
    system = _FakeSystem()
    system.named["worker"] = tuple(
        ProcInfo(pid=100 + i, name="worker") for i in range(6)
    )
    verbs = _verbs(system)
    pending = verbs["system.proc.kill"].handler(
        _intent("system.proc.kill", {"name": "worker", "force": True}),
        _context(),
    )
    assert pending.status is Status.NEEDS_CONFIRM
    done = verbs["system.proc.kill"].handler(
        _intent(
            "system.proc.kill",
            {"name": "worker", "force": True},
            modifiers=frozenset({"confirmed"}),
        ),
        _context(),
    )
    assert done.status is Status.OK
    assert any(c[0] == "kill_pids" for c in system.calls)


def test_trash_and_wifi_need_confirm() -> None:
    system = _FakeSystem()
    verbs = _verbs(system)
    trash = verbs["system.trash.empty"].handler(
        _intent("system.trash.empty"),
        _context(),
    )
    wifi = verbs["system.wifi.set"].handler(
        _intent("system.wifi.set", {"enabled": False}),
        _context(),
    )
    assert trash.status is Status.NEEDS_CONFIRM
    assert wifi.status is Status.NEEDS_CONFIRM
    assert not any(c[0] in {"trash_empty", "wifi"} for c in system.calls)


def test_wifi_confirmed_sets_undo() -> None:
    system = _FakeSystem()
    verbs = _verbs(system)
    result = verbs["system.wifi.set"].handler(
        _intent(
            "system.wifi.set",
            {"enabled": False},
            modifiers=frozenset({"confirmed"}),
        ),
        _context(),
    )
    assert result.status is Status.OK
    assert result.undo is not None
    assert result.undo.slots["enabled"] is True
    assert ("wifi", False) in system.calls


def test_proc_top_is_r0_immediate() -> None:
    system = _FakeSystem()
    verbs = _verbs(system)
    result = verbs["system.proc.top"].handler(
        _intent("system.proc.top"),
        _context(),
    )
    assert result.status is Status.OK
    assert ("open_process_monitor", None) in system.calls


def test_patterns_cover_canonical_utterances() -> None:
    patterns = procs_patterns()
    assert match("free port 3000", patterns)[0] == "system.port.free"
    assert match("kill the process named node", patterns)[0] == "system.proc.kill"
    assert match("empty the trash", patterns)[0] == "system.trash.empty"
    assert match("turn off wi-fi", patterns)[0] == "system.wifi.set"
