"""Cross-cutting invariants from spec §13.5 (T0.7).

These tests are the structural guard rail for slices S1–S8. Invariants that
cannot be enforced until later slices are present as skipped tests with reasons.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from vaani.apps import launch_app, resolve_app
from vaani.intent.router import Router
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
from vaani.policy.undo import UndoStack, register_undo
from vaani.verbs.packs.core import build_core_registry
from vaani.verbs.registry import Registry

SRC_ROOT = Path(__file__).resolve().parents[2] / "src"
VAANI_ROOT = SRC_ROOT / "vaani"
UTTERANCES = Path(__file__).resolve().parents[1] / "data" / "utterances.yaml"

# Plan §2.2 — only modules that exist (or will) under these prefixes are checked.
_LAYER_PREFIXES: tuple[tuple[str, int], ...] = (
    ("vaani.surface.bridge", 8),
    ("vaani.surface.cli", 8),
    ("vaani.surface", 6),
    ("vaani.policy.audit", 7),
    ("vaani.history", 7),
    ("vaani.policy", 4),
    ("vaani.exec", 5),
    ("vaani.verbs", 3),
    ("vaani.context", 2),
    ("vaani.intent", 1),
)

# S0 exception: intent modules hold/filter a Registry for enabled verbs (L1 → L3).
_ALLOWED_IMPORTS: frozenset[tuple[str, str]] = frozenset(
    {
        ("vaani.intent.router", "vaani.verbs.registry"),
        ("vaani.intent.catalog_card", "vaani.verbs.registry"),
        ("vaani.intent.llm", "vaani.verbs.registry"),
        ("vaani.intent.plan_exec", "vaani.verbs.registry"),
        ("vaani.intent.plan_exec", "vaani.policy.confirm"),
    }
)

_SUBPROCESS_FUNCS = frozenset(
    {
        "run",
        "Popen",
        "call",
        "check_call",
        "check_output",
    }
)


def _load_utterances(path: Path) -> list[dict[str, object]]:
    """Minimal YAML subset loader (no PyYAML dependency)."""
    rows: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if line.lstrip().startswith("- "):
            if current is not None:
                rows.append(current)
            current = {}
            rest = line.lstrip()[2:].strip()
            if rest and ":" in rest:
                key, value = rest.split(":", 1)
                current[key.strip()] = _parse_scalar(value.strip())
            continue
        if current is None or ":" not in line:
            continue
        key, value = line.strip().split(":", 1)
        current[key.strip()] = _parse_scalar(value.strip())
    if current is not None:
        rows.append(current)
    return rows


def _parse_scalar(value: str) -> object:
    if value in {"", "null", "~"}:
        return None
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    try:
        return int(value)
    except ValueError:
        return value


def _core_registry() -> Registry:
    registry, _patterns = build_core_registry(
        resolve_app_fn=resolve_app,
        launch_app_fn=launch_app,
        resolve_site_fn=resolve_site,
        open_browser_fn=lambda **_k: "Opened browser.",
    )
    return registry


def _file_module(path: Path) -> str:
    rel = path.relative_to(SRC_ROOT).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _layer_of(mod: str) -> int | None:
    for prefix, layer in _LAYER_PREFIXES:
        if mod == prefix or mod.startswith(prefix + "."):
            return layer
    return None


def _resolve_import_from(current_mod: str, node: ast.ImportFrom) -> str | None:
    if node.level == 0:
        return node.module
    parts = current_mod.split(".")
    # Package __init__ modules are already stripped to the package name.
    pkg = parts if current_mod == "vaani" or (VAANI_ROOT / "/".join(parts[1:])).is_dir() else parts[:-1]
    if node.level > 1:
        drop = node.level - 1
        pkg = pkg[:-drop] if drop <= len(pkg) else []
    if node.module:
        return ".".join([*pkg, *node.module.split(".")])
    return ".".join(pkg) if pkg else None


def _iter_vaani_imports(path: Path) -> list[tuple[str, int]]:
    """Return (imported_module, lineno) for vaani imports in ``path``."""
    current = _file_module(path)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[str, int]] = []
    for node in tree.body:
        # Top-level only — TYPE_CHECKING blocks are still walked via If.
        pass
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "vaani" or alias.name.startswith("vaani."):
                    found.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            target = _resolve_import_from(current, node)
            if target and (target == "vaani" or target.startswith("vaani.")):
                found.append((target, node.lineno))
    return found


def _is_string_expr(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return True
    if isinstance(node, ast.JoinedStr):
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _is_string_expr(node.left) or _is_string_expr(node.right)
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "join":
            return True
        if isinstance(func, ast.Name) and func.id == "join":
            return True
    return False


def _call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _subprocess_module_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr in _SUBPROCESS_FUNCS:
        if isinstance(func.value, ast.Name) and func.value.id == "subprocess":
            return True
    return False


# --- §13.5.1 No shell=True, no string-joined argv ---------------------------------


def test_no_shell_true_in_src() -> None:
    """Invariant 1a: reject real ``shell=True`` call kwargs in ``src/``."""
    offenders: list[str] = []
    for path in SRC_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if kw.arg != "shell":
                    continue
                if isinstance(kw.value, ast.Constant) and kw.value.value is True:
                    rel = path.relative_to(SRC_ROOT.parent)
                    offenders.append(f"{rel}:{node.lineno}")
    assert offenders == []


def test_no_joined_argv_subprocess_commands() -> None:
    """Invariant 1b: subprocess helpers must receive argv sequences, not strings.

    ``powershell()`` may join slots into a *single* ``-Command`` argument while
    still returning a real argv tuple — that helper is whitelisted by path+name.
    """
    offenders: list[str] = []
    for path in VAANI_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _call_name(node)
            # os.system / os.popen always take a shell string.
            if isinstance(node.func, ast.Attribute) and isinstance(
                node.func.value, ast.Name
            ):
                if node.func.value.id == "os" and node.func.attr in {"system", "popen"}:
                    rel = path.relative_to(SRC_ROOT.parent)
                    offenders.append(f"{rel}:{node.lineno}:os.{node.func.attr}")
                    continue
            if not (_subprocess_module_call(node) or name in _SUBPROCESS_FUNCS):
                # Only attribute calls on subprocess, or names bound from it —
                # restrict to subprocess.* to avoid false positives on unrelated run().
                if not _subprocess_module_call(node):
                    continue
            # First positional arg or args=
            cmd_expr: ast.AST | None = node.args[0] if node.args else None
            for kw in node.keywords:
                if kw.arg in {"args", "cmd"}:
                    cmd_expr = kw.value
            if cmd_expr is not None and _is_string_expr(cmd_expr):
                rel = path.relative_to(SRC_ROOT.parent)
                offenders.append(f"{rel}:{node.lineno}:string-command")
    assert offenders == []


def _join_looks_like_argv(node: ast.Call) -> bool:
    """True when ``" ".join(x)`` looks like joining an argv sequence."""
    if not node.args:
        return False
    for sub in ast.walk(node.args[0]):
        # Only argv — ``command``/``args`` are too ambiguous (utterance normalize).
        if isinstance(sub, ast.Name) and sub.id == "argv":
            return True
        if isinstance(sub, ast.Attribute) and sub.attr == "argv":
            return True
    return False


def test_no_shell_join_of_argv_outside_powershell() -> None:
    """Invariant 1c: ``" ".join(argv|args)`` must not build shell command strings.

    Normalization joins (e.g. whitespace collapsing) are fine. The sole allowed
    argv join is ``vaani.exec.runner.powershell``, which quotes slots into a
    PowerShell ``-Command`` argument inside an argv tuple.
    """
    offenders: list[str] = []
    for path in VAANI_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (
                isinstance(func, ast.Attribute)
                and func.attr == "join"
                and isinstance(func.value, ast.Constant)
                and func.value.value == " "
            ):
                continue
            if not _join_looks_like_argv(node):
                continue
            # Whitelist powershell helper (the one argv→string Command site).
            if path.name == "runner.py" and "exec" in path.parts:
                continue
            # Whitelist CLI human/JSON display of argv (never passed to a shell).
            if path.name == "cli.py" and path.parent.name == "vaani":
                continue
            rel = path.relative_to(SRC_ROOT.parent)
            offenders.append(f"{rel}:{node.lineno}")
    assert offenders == []


# --- §13.5 / plan §2.2 Import-graph layering --------------------------------------


def test_import_graph_layering() -> None:
    """Lower layers must not import higher layers (plan §2.2).

    Only edges where *both* ends have an assigned layer are enforced. Unlayered
    modules (controller assembly, platform adapters, apps/sites helpers) are
    ignored so S0's current tree stays green.
    """
    violations: list[str] = []
    for path in VAANI_ROOT.rglob("*.py"):
        current = _file_module(path)
        current_layer = _layer_of(current)
        if current_layer is None:
            continue
        for imported, lineno in _iter_vaani_imports(path):
            imported_layer = _layer_of(imported)
            if imported_layer is None:
                continue
            if imported_layer <= current_layer:
                continue
            if (current, imported) in _ALLOWED_IMPORTS:
                continue
            # Allow importing a parent/same-package prefix at the same layer.
            if imported_layer == current_layer:
                continue
            rel = path.relative_to(SRC_ROOT.parent)
            violations.append(
                f"{rel}:{lineno}: L{current_layer} {current} imports "
                f"L{imported_layer} {imported}"
            )
    assert violations == []


def test_schema_stays_at_layer_floor() -> None:
    """intent.schema may import PlatformId only (plan §2.2)."""
    path = VAANI_ROOT / "intent" / "schema.py"
    imports = [mod for mod, _ in _iter_vaani_imports(path)]
    assert imports == ["vaani.platform.protocol"]


# --- §13.5.3 Platform matrix completeness ----------------------------------------


def test_every_registered_verb_declares_all_three_platforms() -> None:
    """Invariant 3: every verb has an explicit support cell for linux/macos/windows."""
    platforms = (PlatformId.LINUX, PlatformId.MACOS, PlatformId.WINDOWS)
    registry = _core_registry()
    missing: list[str] = []
    for name, row in registry.matrix().items():
        for platform in platforms:
            if platform.value not in row:
                missing.append(f"{name}/{platform.value}")
            # matrix() fills defaults, so also require the Verb.support mapping itself.
            verb = registry.get(name)
            assert verb is not None
            if platform not in verb.support:
                missing.append(f"{name}/{platform.value}:absent-from-verb.support")
    assert missing == []


# --- §13.5 / capability honesty: DEGRADED needs a reason -------------------------


def test_degraded_requires_nonempty_reason() -> None:
    """DEGRADED cells must carry a non-empty reason note in the capability matrix.

    Support is an enum without an attached reason field; reasons surface via
    ``Registry.matrix()`` notes (and later ``vaani caps``).
    """
    registry = _core_registry()
    blank: list[str] = []
    for verb_name, row in registry.matrix().items():
        for platform, (support, note) in row.items():
            if support is Support.DEGRADED and not str(note).strip():
                blank.append(f"{verb_name}/{platform}")
    assert blank == []


def test_degraded_reason_contract_on_synthetic_verb() -> None:
    """Contract: a DEGRADED registration must expose a non-empty matrix note."""

    def _handler(_intent: Intent, _context: Context) -> Result:
        return Result(status=Status.UNSUPPORTED, summary="n/a", rung=1)

    registry = Registry()
    registry.register(
        Verb(
            name="system.volume.set",
            title="Windows volume needs a helper",
            slots={},
            rung=1,
            risk=RiskClass.R0,
            requires=frozenset(),
            support={
                PlatformId.LINUX: Support.SUPPORTED,
                PlatformId.MACOS: Support.SUPPORTED,
                PlatformId.WINDOWS: Support.DEGRADED,
            },
            undo=None,
            pack="core",
            handler=_handler,
        )
    )
    support, note = registry.matrix()["system.volume.set"]["windows"]
    assert support is Support.DEGRADED
    assert note.strip()


# --- §13.5.2 Corpus resolves with brain mocked to raise --------------------------


def test_rung_1_2_corpus_resolves_with_brain_mocked_to_raise() -> None:
    """Invariant 2: rung-1/2 corpus rows never call the LLM parser."""

    def boom(_text: str) -> Intent | None:
        raise AssertionError("LLM parser must not be called for rung 1/2")

    registry, patterns = build_core_registry(
        resolve_app_fn=resolve_app,
        launch_app_fn=launch_app,
        resolve_site_fn=resolve_site,
        open_browser_fn=lambda **_k: "Opened browser.",
    )
    patterns = patterns + register_undo(registry, UndoStack())
    router = Router(
        registry,
        patterns,
        resolve_app=resolve_app,
        resolve_site=resolve_site,
        llm_parse=boom,
    )
    for row in _load_utterances(UTTERANCES):
        verb = row.get("verb")
        rung = row.get("rung")
        if verb in {None, "agent.task"}:
            continue
        if not isinstance(rung, int) or rung > 2:
            continue
        intent = router.route(str(row["utterance"]), platform=PlatformId.LINUX)
        assert intent is not None, row
        assert intent.verb == verb, row
        assert intent.rung == rung, row
        assert router.llm_parse is boom


# --- §13.5.4 R2+ requires PendingAction -------------------------------------------


def test_r2_plus_verb_requires_pending_action() -> None:
    """Invariant 4: R2+ verbs stage a PendingAction; handler runs only after approve."""
    from types import SimpleNamespace
    from vaani.controller import Controller
    from vaani.policy.confirm import requires_confirm

    calls: list[str] = []

    def handler(intent: Intent, context: Context) -> Result:
        calls.append(intent.verb)
        return Result(status=Status.OK, summary="quit", rung=1)

    class Rec:
        def start(self):
            return SimpleNamespace(path=Path("/tmp/a.wav"), duration_seconds=1)

        def stop(self):
            return SimpleNamespace(path=Path("/tmp/a.wav"), duration_seconds=1)

        def cleanup(self):
            pass

    class Groq:
        def transcribe(self, *a, **k):
            return SimpleNamespace(text="quit Slack", language="en")

        def close(self):
            pass

    class Delivery:
        def deliver(self, text, snapshot=None):
            return "ok"

        def cancel(self):
            pass

    class History:
        def insert(self, **kw):
            return 1

        def close(self):
            pass

    c = Controller(
        recorder=Rec(),
        groq=Groq(),
        delivery=Delivery(),
        history=History(),
        key_provider=lambda: "key",
    )
    verb = c.registry.get("app.quit")
    assert verb is not None
    assert requires_confirm(verb.risk)
    object.__setattr__(verb, "handler", handler)
    assert c.trigger_assistant()
    assert c.stop()
    if c._worker:
        c._worker.join(2)
    assert calls == []
    pending = c.confirm.peek()
    assert pending is not None
    assert pending.verb == "app.quit"
    assert c.approve_pending(via="pill")
    assert calls == ["app.quit"]
    c.shutdown()


# --- §13.5.5 UNSUPPORTED never escalates (partial now) ----------------------------


def test_unsupported_platform_excluded_from_enabled() -> None:
    """Partial invariant 5: UNSUPPORTED cells are not enabled on that platform."""

    def _handler(_intent: Intent, _context: Context) -> Result:
        return Result(status=Status.UNSUPPORTED, summary="no", rung=1)

    registry = Registry()
    registry.register(
        Verb(
            name="window.tile",
            title="No tiling on this OS",
            slots={},
            rung=1,
            risk=RiskClass.R0,
            requires=frozenset(),
            support={
                PlatformId.LINUX: Support.SUPPORTED,
                PlatformId.MACOS: Support.SUPPORTED,
                PlatformId.WINDOWS: Support.UNSUPPORTED,
            },
            undo=None,
            pack="core",
            handler=_handler,
        )
    )
    names = {verb.name for verb in registry.enabled(PlatformId.WINDOWS)}
    assert "window.tile" not in names
    assert "window.tile" in {verb.name for verb in registry.enabled(PlatformId.LINUX)}


@pytest.mark.skip(
    reason=(
        "Full invariant 5 (UNSUPPORTED must not fall through to agent.task) needs "
        "router/policy work beyond S0: grammar hits for disabled verbs currently "
        "fall through to the rung-6 agent fallback."
    )
)
def test_unsupported_never_escalates_to_higher_rung() -> None:
    raise AssertionError("unreachable until router refuses unsupported hits")


# --- §13.5.6 No screen frames / secrets in durable storage ------------------------


def test_screen_frame_has_no_disk_write_helpers() -> None:
    """Invariant 6a: ScreenFrame is in-memory only — no write_* on the type."""
    from vaani.intent.schema import ScreenFrame

    writers = [
        name
        for name, _ in inspect.getmembers(ScreenFrame, predicate=callable)
        if "write" in name or "save" in name or "dump" in name
    ]
    assert writers == []


def _is_bytesio_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr == "BytesIO":
        return True
    if isinstance(func, ast.Name) and func.id == "BytesIO":
        return True
    return False


def _is_path_like_expr(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return True
    if isinstance(node, ast.JoinedStr):
        return True
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name) and func.id in {"Path", "PurePath"}:
            return True
        if isinstance(func, ast.Attribute) and func.attr in {
            "join",
            "with_suffix",
            "with_name",
            "with_stem",
            "resolve",
        }:
            return True
    return False


def _assignment_target_names(targets: list[ast.expr]) -> list[str]:
    return [target.id for target in targets if isinstance(target, ast.Name)]


def _names_from_assignments(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[set[str], set[str]]:
    bytesio_names: set[str] = set()
    path_names: set[str] = set()
    for node in ast.walk(func):
        if not isinstance(node, ast.Assign):
            continue
        if _is_bytesio_call(node.value):
            for name in _assignment_target_names(node.targets):
                bytesio_names.add(name)
        elif _is_path_like_expr(node.value):
            for name in _assignment_target_names(node.targets):
                path_names.add(name)
    return bytesio_names, path_names


def _save_writes_disk(
    call: ast.Call,
    *,
    bytesio_names: set[str],
    path_names: set[str],
) -> bool:
    if not call.args:
        return True
    first = call.args[0]
    if _is_bytesio_call(first):
        return False
    if isinstance(first, ast.Name):
        if first.id in bytesio_names:
            return False
        if first.id in path_names:
            return True
        return False
    return _is_path_like_expr(first)


def _persistence_call_writes_disk(
    call: ast.Call,
    *,
    bytesio_names: set[str],
    path_names: set[str],
) -> bool:
    assert isinstance(call.func, ast.Attribute)
    method = call.func.attr
    if method in {"write_bytes", "write_text"}:
        return True
    if method == "save":
        return _save_writes_disk(call, bytesio_names=bytesio_names, path_names=path_names)
    if method == "imwrite":
        if not call.args:
            return True
        first = call.args[0]
        if isinstance(first, ast.Name) and first.id in path_names:
            return True
        return _is_path_like_expr(first)
    return False


def _screen_frame_persistence_offenders(path: Path, tree: ast.AST) -> list[str]:
    persist_names = {"write_bytes", "write_text", "save", "imwrite"}
    rel = path.relative_to(SRC_ROOT.parent)
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        bytesio_names, path_names = _names_from_assignments(node)
        for child in ast.walk(node):
            if not isinstance(child, ast.Call) or not isinstance(child.func, ast.Attribute):
                continue
            if child.func.attr not in persist_names:
                continue
            if _persistence_call_writes_disk(
                child, bytesio_names=bytesio_names, path_names=path_names
            ):
                offenders.append(f"{rel}:{child.lineno}:{child.func.attr}")
    return offenders


def test_no_screen_frame_persistence_calls_in_src() -> None:
    """Invariant 6a: modules that reference ScreenFrame must not persist frames."""
    offenders: list[str] = []
    for path in VAANI_ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "ScreenFrame" not in text:
            continue
        tree = ast.parse(text, filename=str(path))
        offenders.extend(_screen_frame_persistence_offenders(path, tree))
    assert offenders == []


def test_history_schema_has_no_secret_or_screen_columns() -> None:
    """Invariant 6b: history migration must not grow secret/screen columns."""
    history_path = VAANI_ROOT / "history.py"
    tree = ast.parse(history_path.read_text(encoding="utf-8"))
    sql_blobs: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if "CREATE TABLE" in node.value or "ALTER TABLE" in node.value:
                sql_blobs.append(node.value.casefold())
    joined = "\n".join(sql_blobs)
    for forbidden in ("password", "api_key", "token", "secret", "screen", "frame"):
        assert forbidden not in joined


# --- §13.5.7 Results name rung (workspace deferred) -------------------------------


def test_core_verb_results_name_rung() -> None:
    """Invariant 7 (partial): every core handler Result reports a rung."""
    from vaani.policy.dryrun import dispatch

    registry = _core_registry()
    context = Context(
        platform=PlatformId.LINUX,
        workspace=None,
        workspace_source="home",
        repo=None,
        project=None,
        focus=None,
        screen=None,
        session=None,
    )
    for verb in registry.enabled(PlatformId.LINUX):
        intent = Intent(
            verb=verb.name,
            slots={"prompt": "x", "name": "Terminal", "url": "https://example.com"},
            rung=verb.rung,
            confidence=1.0,
            source="test",
            mode="act",
            utterance="test",
            raw_utterance="test",
            modifiers=frozenset(),
            brain=None,
        )
        # Handlers may fail without real I/O; only the Result shape matters.
        try:
            result = dispatch(verb, intent, context)
        except Exception:
            continue
        assert isinstance(result, Result)
        assert isinstance(result.rung, int)
        assert result.rung == verb.rung


def test_every_result_names_workspace() -> None:
    """Invariant 7: every dispatched Result carries workspace_source."""
    from pathlib import Path

    from vaani.policy.dryrun import dispatch

    registry = _core_registry()
    context = Context(
        platform=PlatformId.LINUX,
        workspace=Path("/tmp/ws"),
        workspace_source="config",
        repo=None,
        project=None,
        focus=None,
        screen=None,
        session=None,
    )
    named = 0
    for verb in registry.enabled(PlatformId.LINUX):
        intent = Intent(
            verb=verb.name,
            slots={"prompt": "x", "name": "Terminal", "url": "https://example.com"},
            rung=verb.rung,
            confidence=1.0,
            source="test",
            mode="act",
            utterance="test",
            raw_utterance="test",
            modifiers=frozenset(),
            brain=None,
        )
        try:
            result = dispatch(verb, intent, context)
        except Exception:
            continue
        assert result.workspace_source == "config"
        assert result.workspace == Path("/tmp/ws")
        named += 1
    assert named > 0
