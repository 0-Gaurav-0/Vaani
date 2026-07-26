"""Project profile detection from manifests / lockfiles (spec §12.8 / T3.3).

Detection order is explicit and reported. A miss refuses with the list of files
checked — never guesses ``npm test`` in a Python-only repo.
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vaani.intent.schema import ProjectProfile

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - repo targets 3.12; tomllib backports via stdlib only
    import tomllib  # type: ignore[no-redef,attr-defined]

# Files / dirs examined for every detection (refusal message names these).
CHECKED_FILES: tuple[str, ...] = (
    "vaani.toml",
    "pnpm-lock.yaml",
    "yarn.lock",
    "package-lock.json",
    "npm-shrinkwrap.json",
    "bun.lock",
    "bun.lockb",
    "package.json",
    "uv.lock",
    "poetry.lock",
    "Pipfile.lock",
    "Pipfile",
    "pyproject.toml",
    "requirements.txt",
    "Makefile",
    "makefile",
    "manage.py",
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
    ".env",
    ".env.local",
    ".env.development",
    ".env.production",
    "tests",
    "test",
)

_COMPOSE_NAMES: tuple[str, ...] = (
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
)

_ENV_NAMES: tuple[str, ...] = (
    ".env",
    ".env.local",
    ".env.development",
    ".env.production",
)

# Lockfile / manifest → package manager (order is priority).
_LOCKFILE_MANAGERS: tuple[tuple[str, str], ...] = (
    ("pnpm-lock.yaml", "pnpm"),
    ("yarn.lock", "yarn"),
    ("package-lock.json", "npm"),
    ("npm-shrinkwrap.json", "npm"),
    ("bun.lock", "bun"),
    ("bun.lockb", "bun"),
    ("uv.lock", "uv"),
    ("poetry.lock", "poetry"),
    ("Pipfile.lock", "pipenv"),
)

_MAKE_TARGET = re.compile(r"^([A-Za-z_][\w-]*)\s*:")

_COMMAND_FIELDS = ("test", "build", "dev", "typecheck", "lint", "format")

# Cache: resolved root → (mtime fingerprint, result)
_cache: dict[Path, tuple[tuple[tuple[str, float | None], ...], ProjectProfile | ProfileMiss]] = {}


@dataclass(frozen=True)
class ProfileMiss:
    """No project tooling detected — refuse rather than guess."""

    root: Path
    checked: tuple[str, ...] = CHECKED_FILES

    def refusal_message(self) -> str:
        listed = ", ".join(self.checked)
        return (
            f"No project profile detected in {self.root}. "
            f"Checked: {listed}. "
            "Add a vaani.toml override or a recognized manifest "
            "(package.json, pyproject.toml, Makefile, …)."
        )


def clear_project_cache() -> None:
    """Drop the in-process profile cache (tests / after external edits)."""
    _cache.clear()


def detect_project(root: Path, *, use_cache: bool = True) -> ProjectProfile | ProfileMiss:
    """Detect ``ProjectProfile`` for ``root``, or ``ProfileMiss`` if nothing matches.

    Cached by resolved root with mtime invalidation over :data:`CHECKED_FILES`.
    """
    try:
        resolved = root.expanduser().resolve()
    except OSError:
        return ProfileMiss(root=root, checked=CHECKED_FILES)
    if not resolved.is_dir():
        return ProfileMiss(root=resolved, checked=CHECKED_FILES)

    fingerprint = _fingerprint(resolved)
    if use_cache:
        cached = _cache.get(resolved)
        if cached is not None and cached[0] == fingerprint:
            return cached[1]

    result = _detect_uncached(resolved)
    if use_cache:
        _cache[resolved] = (fingerprint, result)
    return result


def require_command(
    root: Path,
    field: str,
    *,
    use_cache: bool = True,
) -> tuple[str, ...] | ProfileMiss:
    """Resolve one argv command field, or refuse naming the files checked.

    Used by project verbs (T3.4). ``field`` is one of test/build/dev/typecheck/lint.
    """
    if field not in _COMMAND_FIELDS:
        raise ValueError(f"unknown project command field: {field!r}")
    detected = detect_project(root, use_cache=use_cache)
    if isinstance(detected, ProfileMiss):
        return detected
    argv = getattr(detected, field)
    if argv:
        return tuple(argv)
    return ProfileMiss(root=detected.root, checked=CHECKED_FILES)


def _fingerprint(root: Path) -> tuple[tuple[str, float | None], ...]:
    items: list[tuple[str, float | None]] = []
    for name in CHECKED_FILES:
        path = root / name
        try:
            if path.exists():
                items.append((name, path.stat().st_mtime))
            else:
                items.append((name, None))
        except OSError:
            items.append((name, None))
    return tuple(items)


def _detect_uncached(root: Path) -> ProjectProfile | ProfileMiss:
    present = {name for name in CHECKED_FILES if (root / name).exists()}
    override = _load_vaani_toml(root)

    # Complete miss: nothing we know how to read, and no override file.
    markers = present - set(_ENV_NAMES)  # .env alone is not a project
    if not markers and override is None:
        return ProfileMiss(root=root, checked=CHECKED_FILES)

    manager = _detect_manager(root, present, override)
    scripts = _package_scripts(root) if "package.json" in present else {}
    pyproject = _load_toml(root / "pyproject.toml") if "pyproject.toml" in present else {}
    make_targets = _makefile_targets(root) if present & {"Makefile", "makefile"} else frozenset()

    test = _resolve_test(root, present, scripts, pyproject, make_targets, manager, override)
    build = _resolve_named(
        "build", root, present, scripts, pyproject, make_targets, manager, override
    )
    dev = _resolve_dev(root, present, scripts, pyproject, make_targets, manager, override)
    typecheck = _resolve_named(
        "typecheck", root, present, scripts, pyproject, make_targets, manager, override
    )
    lint = _resolve_named(
        "lint", root, present, scripts, pyproject, make_targets, manager, override
    )
    format_argv = _resolve_format(
        root, present, scripts, pyproject, make_targets, manager, override
    )

    compose_file = _compose_file(root, present, override)
    env_files = _env_files(root, present, override)

    profile = ProjectProfile(
        root=root,
        manager=manager,
        test=test,
        build=build,
        dev=dev,
        typecheck=typecheck,
        lint=lint,
        format=format_argv,
        compose_file=compose_file,
        env_files=env_files,
    )

    # Override may supply only some fields — already merged above. If the only
    # signal was env files and we still have no manager/commands/compose, miss.
    if (
        manager is None
        and test is None
        and build is None
        and dev is None
        and typecheck is None
        and lint is None
        and format_argv is None
        and compose_file is None
        and override is None
    ):
        return ProfileMiss(root=root, checked=CHECKED_FILES)

    return profile


def _detect_manager(
    root: Path,
    present: set[str],
    override: dict[str, Any] | None,
) -> str | None:
    if override and override.get("manager"):
        return str(override["manager"])

    for filename, manager in _LOCKFILE_MANAGERS:
        if filename in present:
            return manager

    if "Pipfile" in present:
        return "pipenv"

    if "pyproject.toml" in present:
        data = _load_toml(root / "pyproject.toml")
        tool = data.get("tool") if isinstance(data.get("tool"), dict) else {}
        if isinstance(tool, dict):
            if "poetry" in tool:
                return "poetry"
            if "hatch" in tool:
                return "hatch"
            if "uv" in tool:
                return "uv"

    if "package.json" in present:
        return "npm"

    if "requirements.txt" in present or "pyproject.toml" in present:
        return "pip"

    return None


def _resolve_test(
    root: Path,
    present: set[str],
    scripts: dict[str, str],
    pyproject: dict[str, Any],
    make_targets: frozenset[str],
    manager: str | None,
    override: dict[str, Any] | None,
) -> tuple[str, ...] | None:
    if override and override.get("test") is not None:
        return _as_argv(override["test"])

    # package.json scripts.test — only when the file exists (never invent npm).
    if "package.json" in present and "test" in scripts:
        return _js_script_argv(manager, "test")

    py_argv = _python_test_argv(pyproject)
    if py_argv is not None:
        return py_argv

    if "test" in make_targets:
        return ("make", "test")

    if "tests" in present or "test" in present:
        # Directory marker only — pytest is the safe default for a tests/ tree.
        return ("pytest",)

    return None


def _resolve_dev(
    root: Path,
    present: set[str],
    scripts: dict[str, str],
    pyproject: dict[str, Any],
    make_targets: frozenset[str],
    manager: str | None,
    override: dict[str, Any] | None,
) -> tuple[str, ...] | None:
    if override and override.get("dev") is not None:
        return _as_argv(override["dev"])

    if "package.json" in present:
        for name in ("dev", "start"):
            if name in scripts:
                return _js_script_argv(manager, name)

    if "manage.py" in present:
        return ("python", "manage.py", "runserver")

    if "dev" in make_targets:
        return ("make", "dev")
    if "run" in make_targets:
        return ("make", "run")

    # hatch run / poetry scripts are too ambiguous without an explicit name.
    _ = pyproject
    return None


def _resolve_named(
    field: str,
    root: Path,
    present: set[str],
    scripts: dict[str, str],
    pyproject: dict[str, Any],
    make_targets: frozenset[str],
    manager: str | None,
    override: dict[str, Any] | None,
) -> tuple[str, ...] | None:
    if override and override.get(field) is not None:
        return _as_argv(override[field])

    if "package.json" in present and field in scripts:
        return _js_script_argv(manager, field)

    if field == "typecheck":
        tool = pyproject.get("tool") if isinstance(pyproject.get("tool"), dict) else {}
        if isinstance(tool, dict):
            if "mypy" in tool:
                return ("mypy",)
            if "pyright" in tool:
                return ("pyright",)
        if "package.json" in present:
            # tsc only when JS manifest exists — never in a Python-only tree.
            pkg = _load_json(root / "package.json")
            deps = {}
            if isinstance(pkg, dict):
                for key in ("dependencies", "devDependencies"):
                    block = pkg.get(key)
                    if isinstance(block, dict):
                        deps.update(block)
            if "typescript" in deps:
                return ("tsc", "--noEmit")

    if field in make_targets:
        return ("make", field)

    _ = present
    return None


def _resolve_format(
    root: Path,
    present: set[str],
    scripts: dict[str, str],
    pyproject: dict[str, Any],
    make_targets: frozenset[str],
    manager: str | None,
    override: dict[str, Any] | None,
) -> tuple[str, ...] | None:
    """Detect a verifiable project formatter (prettier / ruff / scripts.format)."""
    if override and override.get("format") is not None:
        return _as_argv(override["format"])

    if "package.json" in present and "format" in scripts:
        return _js_script_argv(manager, "format")

    if "package.json" in present:
        pkg = _load_json(root / "package.json")
        deps: dict[str, Any] = {}
        if isinstance(pkg, dict):
            for key in ("dependencies", "devDependencies"):
                block = pkg.get(key)
                if isinstance(block, dict):
                    deps.update(block)
        if "prettier" in deps:
            if manager == "yarn":
                return ("yarn", "prettier", "--write", ".")
            if manager == "bun":
                return ("bunx", "prettier", "--write", ".")
            if manager == "pnpm":
                return ("pnpm", "exec", "prettier", "--write", ".")
            return ("npx", "prettier", "--write", ".")

    tool = pyproject.get("tool") if isinstance(pyproject.get("tool"), dict) else {}
    if isinstance(tool, dict) and "ruff" in tool:
        return ("ruff", "format", ".")

    if "format" in make_targets:
        return ("make", "format")

    _ = present
    return None


def _python_test_argv(pyproject: dict[str, Any]) -> tuple[str, ...] | None:
    tool = pyproject.get("tool") if isinstance(pyproject.get("tool"), dict) else {}
    if not isinstance(tool, dict):
        return None
    if "pytest" in tool:
        return ("pytest",)
    if "hatch" in tool:
        return ("hatch", "test")
    poe = tool.get("poe")
    if isinstance(poe, dict):
        tasks = poe.get("tasks")
        if isinstance(tasks, dict) and "test" in tasks:
            return ("poe", "test")
    return None


def _js_script_argv(manager: str | None, script: str) -> tuple[str, ...]:
    mgr = manager if manager in {"npm", "pnpm", "yarn", "bun"} else "npm"
    if script == "test" and mgr in {"npm", "pnpm", "yarn", "bun"}:
        return (mgr, "test")
    if mgr == "yarn":
        return ("yarn", script)
    if mgr == "bun":
        return ("bun", "run", script)
    # npm / pnpm
    return (mgr, "run", script)


def _package_scripts(root: Path) -> dict[str, str]:
    data = _load_json(root / "package.json")
    if not isinstance(data, dict):
        return {}
    scripts = data.get("scripts")
    if not isinstance(scripts, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in scripts.items():
        if isinstance(key, str) and isinstance(value, str):
            out[key] = value
    return out


def _makefile_targets(root: Path) -> frozenset[str]:
    for name in ("Makefile", "makefile"):
        path = root / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return frozenset()
        targets: set[str] = set()
        for line in text.splitlines():
            if line.startswith("\t") or line.startswith(" ") or line.startswith("#"):
                continue
            match = _MAKE_TARGET.match(line)
            if match:
                targets.add(match.group(1))
        return frozenset(targets)
    return frozenset()


def _compose_file(
    root: Path,
    present: set[str],
    override: dict[str, Any] | None,
) -> Path | None:
    if override and override.get("compose_file"):
        path = root / str(override["compose_file"])
        return path if path.is_file() else path
    for name in _COMPOSE_NAMES:
        if name in present:
            return root / name
    return None


def _env_files(
    root: Path,
    present: set[str],
    override: dict[str, Any] | None,
) -> tuple[Path, ...]:
    if override and override.get("env_files") is not None:
        raw = override["env_files"]
        if isinstance(raw, (list, tuple)):
            return tuple(root / str(item) for item in raw)
    return tuple(root / name for name in _ENV_NAMES if name in present)


def _load_vaani_toml(root: Path) -> dict[str, Any] | None:
    path = root / "vaani.toml"
    if not path.is_file():
        return None
    data = _load_toml(path)
    project = data.get("project")
    if isinstance(project, dict):
        return project
    # Flat keys at top level are also accepted.
    keys = {
        "manager",
        "test",
        "build",
        "dev",
        "typecheck",
        "lint",
        "format",
        "compose_file",
        "env_files",
    }
    if keys & set(data):
        return {k: data[k] for k in keys if k in data}
    return {}


def _load_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def _as_argv(value: Any) -> tuple[str, ...] | None:
    if value is None:
        return None
    if isinstance(value, str):
        parts = value.split()
        return tuple(parts) if parts else None
    if isinstance(value, (list, tuple)):
        if not value:
            return None
        return tuple(str(item) for item in value)
    return None
