"""Verb registry tests (T0.4)."""
from __future__ import annotations

from vaani.apps import launch_app, resolve_app
from vaani.intent.schema import (
    Context,
    Intent,
    Result,
    RiskClass,
    SlotSpec,
    Status,
    Support,
    Verb,
)
from vaani.platform.protocol import PlatformId
from vaani.sites import resolve_site
from vaani.verbs.packs.core import CORE_VERB_NAMES, build_core_registry
from vaani.verbs.packs.procs import PROCS_VERB_NAMES
from vaani.verbs.packs.project import PROJECT_VERB_NAMES
from vaani.verbs.registry import Registry


def _ok(_intent: Intent, _context: Context) -> Result:
    return Result(status=Status.OK, summary="ok")


def test_register_get_enabled_matrix() -> None:
    registry = Registry()
    verb = Verb(
        name="app.open",
        title="Open an application",
        slots={"name": SlotSpec(type="str")},
        rung=1,
        risk=RiskClass.R0,
        requires=frozenset(),
        support={
            PlatformId.LINUX: Support.SUPPORTED,
            PlatformId.MACOS: Support.DEGRADED,
            PlatformId.WINDOWS: Support.UNSUPPORTED,
        },
        undo=None,
        pack="core",
        handler=_ok,
    )
    registry.register(verb)
    assert registry.get("app.open") is verb
    enabled = registry.enabled(PlatformId.LINUX)
    assert [item.name for item in enabled] == ["app.open"]
    assert registry.enabled(PlatformId.WINDOWS) == ()
    matrix = registry.matrix()
    assert matrix["app.open"]["linux"][0] is Support.SUPPORTED
    assert matrix["app.open"]["windows"][0] is Support.UNSUPPORTED


def test_core_pack_registers_t13_verbs() -> None:
    registry, _patterns = build_core_registry(
        resolve_app_fn=resolve_app,
        launch_app_fn=launch_app,
        resolve_site_fn=resolve_site,
        open_browser_fn=lambda **_k: "Opened",
    )
    names = set(registry.matrix())
    from vaani.verbs.packs.window import WINDOW_VERB_NAMES

    assert names == (
        CORE_VERB_NAMES | PROCS_VERB_NAMES | PROJECT_VERB_NAMES | WINDOW_VERB_NAMES
    )
    # DEGRADED cells stay enabled; only UNSUPPORTED is filtered out.
    enabled = {verb.name for verb in registry.enabled(PlatformId.LINUX)}
    assert "app.open" in enabled
    assert "system.volume.set" in enabled
    assert "system.dnd.set" in enabled
    assert "system.port.free" in enabled
    assert "project.test.run" in enabled
