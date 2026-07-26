"""CLI dispatch tests (T0.5)."""
from __future__ import annotations

import json
from typing import Any

import pytest

from vaani.cli import (
    build_registry,
    cmd_caps,
    cmd_do,
    main,
    materialize_argv,
)
from vaani.intent.schema import Context, Intent, Result, RiskClass, SlotSpec, Status, Support, Verb
from vaani.platform.protocol import PlatformId
from vaani.verbs.registry import Registry


def _ok(_intent: Intent, _context: Context) -> Result:
    return Result(status=Status.OK, summary="ok")


def _registry_with(*names: str) -> Registry:
    registry = Registry()
    for name in names:
        registry.register(
            Verb(
                name=name,
                title=name,
                slots={"name": SlotSpec(type="str", required=False)},
                rung=1,
                risk=RiskClass.R0,
                requires=frozenset(),
                support={
                    PlatformId.LINUX: Support.SUPPORTED,
                    PlatformId.MACOS: Support.SUPPORTED,
                    PlatformId.WINDOWS: Support.SUPPORTED,
                },
                undo=None,
                pack="core",
                handler=_ok,
            )
        )
    return registry


def test_do_unknown_verb_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    registry = _registry_with("app.open", "site.open")
    code = cmd_do(
        "no.such.verb",
        [],
        as_json=True,
        registry=registry,
        platform=PlatformId.MACOS,
    )
    assert code == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] == "unknown_verb"
    assert payload["catalog"] == ["app.open", "site.open"]


def test_record_both_spellings(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_record() -> int:
        calls.append("record")
        return 0

    monkeypatch.setattr("vaani.cli.manual_record", fake_record)
    assert main(["--record"]) == 0
    assert main(["record"]) == 0
    assert calls == ["record", "record"]


def test_debug_both_spellings(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[bool] = []

    def fake_daemon(*, debug: bool = False) -> int:
        seen.append(debug)
        return 0

    monkeypatch.setattr("vaani.cli.run_daemon", fake_daemon)
    assert main(["--debug"]) == 0
    assert main(["debug"]) == 0
    assert seen == [True, True]


def test_bare_vaani_runs_daemon(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[bool] = []

    def fake_daemon(*, debug: bool = False) -> int:
        called.append(debug)
        return 0

    monkeypatch.setattr("vaani.cli.run_daemon", fake_daemon)
    assert main([]) == 0
    assert called == [False]


def test_do_json_dry_run_stable(capsys: pytest.CaptureFixture[str]) -> None:
    code = cmd_do(
        "app.open",
        [("name", "Terminal")],
        as_json=True,
        dry_run=True,
        platform=PlatformId.MACOS,
        registry=build_registry(PlatformId.MACOS),
    )
    assert code == 0
    first = capsys.readouterr().out
    payload = json.loads(first)
    assert payload["status"] == "dry_run"
    assert payload["verb"] == "app.open"
    assert payload["slots"] == {"name": "Terminal"}
    assert payload["argv"] == ["open", "-a", "Terminal"]
    assert payload["evidence"] == ["open", "-a", "Terminal"]

    # Stable: same input → identical JSON bytes (sorted keys, compact separators).
    code = cmd_do(
        "app.open",
        [("name", "Terminal")],
        as_json=True,
        dry_run=True,
        platform=PlatformId.MACOS,
        registry=build_registry(PlatformId.MACOS),
    )
    assert code == 0
    second = capsys.readouterr().out
    assert second == first
    assert list(json.loads(second).keys()) == sorted(json.loads(second).keys())


def test_caps_completeness_json(capsys: pytest.CaptureFixture[str]) -> None:
    registry = build_registry(PlatformId.MACOS)
    code = cmd_caps(as_json=True, registry=registry)
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {"app.open", "site.open", "browser.open", "agent.task"}
    for verb_name, row in payload.items():
        assert set(row) == {"linux", "macos", "windows"}, verb_name
        for platform, cell in row.items():
            assert cell.get("support") in {"supported", "degraded", "unsupported"}, (
                verb_name,
                platform,
            )


def test_caps_text_has_no_blank_cells(capsys: pytest.CaptureFixture[str]) -> None:
    registry = build_registry(PlatformId.LINUX)
    assert cmd_caps(as_json=False, registry=registry) == 0
    out = capsys.readouterr().out
    assert "app.open" in out
    assert "supported" in out
    # No empty platform columns — every data line has three support tokens after the verb.
    lines = [line for line in out.splitlines() if line and not line.startswith("-") and not line.startswith("verb")]
    for line in lines:
        parts = line.split()
        assert len(parts) >= 4
        assert all(part in {"supported", "degraded", "unsupported"} for part in parts[1:4])


def test_materialize_app_open_all_platforms() -> None:
    mac = materialize_argv("app.open", {"name": "Terminal"}, platform=PlatformId.MACOS)
    assert mac == ("open", "-a", "Terminal")

    linux = materialize_argv("app.open", {"name": "Terminal"}, platform=PlatformId.LINUX)
    assert linux is not None
    assert linux[0]  # executable name or resolved path
    assert "gnome-terminal" in linux[0] or linux[0].endswith("gnome-terminal")

    win = materialize_argv("app.open", {"name": "Terminal"}, platform=PlatformId.WINDOWS)
    assert win is not None
    assert win[0] in {"wt.exe", "wt"} or win[0].endswith(("wt.exe", "wt"))


def test_subcommand_dispatch_do_and_caps(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[Any, ...]] = []

    def fake_do(*args: Any, **kwargs: Any) -> int:
        seen.append(("do", args, kwargs))
        return 0

    def fake_caps(**kwargs: Any) -> int:
        seen.append(("caps", kwargs))
        return 0

    monkeypatch.setattr("vaani.cli.cmd_do", fake_do)
    monkeypatch.setattr("vaani.cli.cmd_caps", fake_caps)
    assert main(["do", "app.open", "--slot", "name=Terminal", "--json", "--dry-run"]) == 0
    assert main(["caps", "--json"]) == 0
    assert seen[0][0] == "do"
    assert seen[0][1][0] == "app.open"
    assert seen[0][2]["as_json"] is True
    assert seen[0][2]["dry_run"] is True
    assert seen[1] == ("caps", {"as_json": True})


def test_main_module_entry_exports() -> None:
    from vaani import __main__ as mod

    assert callable(mod.main)
    assert callable(mod.manual_record)
