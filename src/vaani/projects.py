"""Open a project folder in an editor by voice: "open vaani in cursor".

Folders are found by spoken name under project roots (home + ~/Gaurav
Projects by default), preferring real projects (.git, package.json, …),
shallow paths and recently touched ones. Explicit aliases live in
``~/.config/vaani/projects.json``: {"vaani": "/abs/path", …}.

Env:
  VAANI_PROJECT_ROOTS   colon-separated "path" or "path=depth" entries
  VAANI_DEFAULT_EDITOR  editor key used when none is spoken (default: cursor)
"""
from __future__ import annotations

import difflib
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

LOGGER = logging.getLogger("vaani")


@dataclass(frozen=True)
class Editor:
    key: str
    name: str
    executables: tuple[str, ...]


# Spoken aliases → editor. Longest alias wins.
EDITORS: tuple[tuple[tuple[str, ...], Editor], ...] = (
    (("visual studio code", "vs code", "vscode", "vs-code", "v s code", "code"), Editor("code", "VS Code", ("code",))),
    (("cursor",), Editor("cursor", "Cursor", ("cursor",))),
    (("antigravity",), Editor("antigravity", "Antigravity", ("antigravity-ide", "antigravity"))),
    (("zed",), Editor("zed", "Zed", ("zed", "zeditor"))),
    (("pycharm",), Editor("pycharm", "PyCharm", ("pycharm", "pycharm-community", "charm"))),
    (("intellij", "idea"), Editor("idea", "IntelliJ IDEA", ("idea", "intellij-idea-community"))),
    (("sublime", "sublime text"), Editor("sublime", "Sublime Text", ("subl",))),
    (("file manager", "files", "nautilus"), Editor("files", "Files", ("nautilus", "xdg-open"))),
)
_EDITOR_BY_KEY = {e.key: e for _aliases, e in EDITORS}

_SKIP_DIRS = frozenset(
    "node_modules .venv venv env __pycache__ site-packages dist build out target .git .cache "
    "snap go .next .nuxt coverage vendor Library AppData wine-11.6".split()
)
_PROJECT_MARKERS = frozenset(
    ".git package.json pyproject.toml Cargo.toml go.mod requirements.txt pom.xml "
    "build.gradle Makefile setup.py .vscode composer.json Gemfile deno.json".split()
)
# Spoken filler that never names the folder.
_NAME_STOP = frozenset(
    "the my a an project projects folder repo repository directory workspace code codebase "
    "wala wali vala ka ki ke".split()
)
INDEX_TTL_S = 600.0
MIN_SCORE = 0.62


@dataclass(frozen=True)
class ProjectMatch:
    name: str
    path: Path
    score: float


@dataclass(frozen=True)
class EditorRequest:
    project: str
    editor: Editor | None  # None → default editor


def _contains_phrase(text: str, phrase: str) -> bool:
    return re.search(rf"(?<![\w]){re.escape(phrase)}(?![\w])", text) is not None


def resolve_editor(text: str) -> Editor | None:
    norm = " ".join((text or "").casefold().split())
    best: tuple[int, Editor] | None = None
    for aliases, editor in EDITORS:
        for alias in aliases:
            if _contains_phrase(norm, alias) and (best is None or len(alias) > best[0]):
                best = (len(alias), editor)
    return best[1] if best else None


def default_editor() -> Editor:
    key = os.environ.get("VAANI_DEFAULT_EDITOR", "cursor").strip().casefold()
    editor = _EDITOR_BY_KEY.get(key) or _EDITOR_BY_KEY["cursor"]
    if not any(shutil.which(x) for x in editor.executables):
        for fallback in ("cursor", "code", "zed"):
            cand = _EDITOR_BY_KEY[fallback]
            if any(shutil.which(x) for x in cand.executables):
                return cand
    return editor


_EDITOR_ALT = "|".join(
    sorted({re.escape(a) for aliases, _e in EDITORS for a in aliases}, key=len, reverse=True)
)
_OPEN = r"(?:open|launch|start|initiate|resume|kholo|khol\s*do|khol|open\s+karo|open\s+kar\s+do)"
_POLITE = r"(?:(?:please|pls|can\s+you|could\s+you|zara|jaldi)\s+)*"
# "open vaani project in cursor" / "open the flow repo with vs code"
_EN_RE = re.compile(
    rf"^{_POLITE}{_OPEN}\s+(?:up\s+)?(?P<proj>.+?)\s+(?:in|with|on|using)\s+(?P<ed>{_EDITOR_ALT})\b",
    re.I,
)
# "vaani project cursor me kholo" / "flow repo ko vs code mein open karo"
_HI_RE = re.compile(
    rf"^{_POLITE}(?P<proj>.+?)\s+(?:ko\s+)?(?P<ed>{_EDITOR_ALT})\s+(?:me|mein|mai|main|pe|par)\s+{_OPEN}\b",
    re.I,
)
# "open vaani project" / "open the trueforge repo" (no editor → default)
_KEYWORD_RE = re.compile(
    rf"^{_POLITE}{_OPEN}\s+(?:up\s+)?(?:the\s+|my\s+)?(?P<proj>.+?)\s+(?:project|repo|repository|codebase|workspace|session)\b",
    re.I,
)
# "initiate project vani"
_KEYWORD_FIRST_RE = re.compile(
    rf"^{_POLITE}{_OPEN}\s+(?:the\s+|my\s+)?(?:project|repo|repository|codebase|workspace)\s+(?P<proj>.+)$",
    re.I,
)
_KEYWORD_HI_RE = re.compile(
    rf"^{_POLITE}(?P<proj>.+?)\s+(?:project|repo|repository|codebase)\s+{_OPEN}\b",
    re.I,
)


def parse_editor_request(text: str) -> EditorRequest | None:
    """Deterministic parse of "open <project> in <editor>" style commands."""
    raw = " ".join((text or "").strip().rstrip(".!?").split())
    for regex in (_EN_RE, _HI_RE):
        m = regex.search(raw)
        if m:
            proj = m.group("proj").strip()
            ed = resolve_editor(m.group("ed"))
            if proj and ed is not None and ed.key != "files":
                return EditorRequest(proj, ed)
    for regex in (_KEYWORD_RE, _KEYWORD_FIRST_RE, _KEYWORD_HI_RE):
        m = regex.search(raw)
        if m and m.group("proj").strip():
            return EditorRequest(m.group("proj").strip(), None)
    return None


def _norm_tokens(text: str) -> list[str]:
    # Split camelCase, separators; drop filler.
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text or "")
    words = re.findall(r"[a-z0-9]+", text.casefold())
    return [w for w in words if w not in _NAME_STOP]


def _roots() -> list[tuple[Path, int]]:
    raw = os.environ.get("VAANI_PROJECT_ROOTS", "").strip()
    home = Path.home()
    if not raw:
        return [(home, 2), (home / "Gaurav Projects", 4), (home / "Projects", 3), (home / "code", 3)]
    roots: list[tuple[Path, int]] = []
    for part in raw.split(":"):
        if not part.strip():
            continue
        path, _, depth = part.partition("=")
        try:
            roots.append((Path(path).expanduser(), int(depth) if depth else 3))
        except ValueError:
            roots.append((Path(path).expanduser(), 3))
    return roots


@dataclass(frozen=True)
class _Entry:
    path: Path
    tokens: tuple[str, ...]
    is_project: bool
    depth: int
    mtime: float


class ProjectIndex:
    """Cached folder index; refreshes in the background after INDEX_TTL_S."""

    def __init__(self, roots: list[tuple[Path, int]] | None = None, *, clock=time.monotonic):
        self._roots = roots
        self._clock = clock
        self._entries: list[_Entry] = []
        self._built_at = -1e9
        self._lock = threading.Lock()
        self._refreshing = False

    def build(self) -> None:
        entries: list[_Entry] = []
        home = Path.home()
        seen: set[Path] = set()
        for root, max_depth in self._roots or _roots():
            if not root.is_dir():
                continue
            for dirpath, dirs, files in os.walk(root, followlinks=False):
                here = Path(dirpath)
                try:
                    depth = len(here.relative_to(root).parts)
                except ValueError:
                    depth = 0
                dirs[:] = [d for d in dirs if not d.startswith(".") and d not in _SKIP_DIRS]
                if depth >= max_depth:
                    dirs[:] = []
                if here in (home, root):
                    continue
                try:
                    real = here.resolve()
                except OSError:
                    real = here
                if real in seen:
                    continue
                seen.add(real)
                names = set(dirs) | set(files)
                try:
                    mtime = here.stat().st_mtime
                except OSError:
                    mtime = 0.0
                entries.append(
                    _Entry(here, tuple(_norm_tokens(here.name)), bool(names & _PROJECT_MARKERS), depth, mtime)
                )
            # Top-level symlinks (e.g. ~/Vaani → repo) are skipped by os.walk.
            try:
                for child in root.iterdir():
                    if child.is_symlink() and child.is_dir() and not child.name.startswith("."):
                        real = child.resolve()
                        if real not in seen:
                            seen.add(real)
                            entries.append(_Entry(child, tuple(_norm_tokens(child.name)), True, 1, 0.0))
            except OSError:
                pass
        with self._lock:
            self._entries = entries
            self._built_at = self._clock()
        LOGGER.info("event=project_index_built dirs=%s projects=%s", len(entries), sum(e.is_project for e in entries))

    def _ensure(self) -> list[_Entry]:
        with self._lock:
            fresh = self._clock() - self._built_at < INDEX_TTL_S
            have = bool(self._entries)
        if not have:
            self.build()
        elif not fresh and not self._refreshing:
            self._refreshing = True

            def refresh() -> None:
                try:
                    self.build()
                finally:
                    self._refreshing = False

            threading.Thread(target=refresh, daemon=True, name="vaani-project-index").start()
        with self._lock:
            return list(self._entries)

    def find(self, spoken: str) -> ProjectMatch | None:
        alias = _alias_lookup(spoken)
        if alias is not None:
            return alias
        want = _norm_tokens(spoken)
        if not want:
            return None
        want_joined = "".join(want)
        now = time.time()
        best: tuple[float, _Entry] | None = None
        for entry in self._ensure():
            if not entry.tokens:
                continue
            joined = "".join(entry.tokens)
            ratio = max(
                difflib.SequenceMatcher(None, want_joined, joined).ratio(),
                difflib.SequenceMatcher(None, " ".join(want), " ".join(entry.tokens)).ratio(),
            )
            # Every spoken word appears in the folder name (e.g. "api navbar").
            hits = sum(
                1 for w in want if any(difflib.SequenceMatcher(None, w, t).ratio() >= 0.8 for t in entry.tokens)
            )
            coverage = hits / len(want)
            score = max(ratio, 0.55 + 0.4 * coverage if coverage == 1.0 else ratio)
            if score < MIN_SCORE:
                continue
            score += 0.08 if entry.is_project else 0.0
            score -= 0.02 * max(0, entry.depth - 1)
            if entry.mtime and now - entry.mtime < 14 * 86400:
                score += 0.03
            if best is None or score > best[0]:
                best = (score, entry)
        if best is None:
            return None
        return ProjectMatch(best[1].path.name, best[1].path, round(best[0], 3))


def _alias_lookup(spoken: str) -> ProjectMatch | None:
    config = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "vaani" / "projects.json"
    aliases: dict[str, str] = {}
    try:
        data = json.loads(config.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            aliases = {str(k).casefold(): str(v) for k, v in data.items()}
    except (OSError, ValueError):
        pass
    # Built-in: ~/Vaani is this machine's Vaani workspace.
    for spoken_name in ("vaani", "vani", "wani", "vaani session", "vani session"):
        aliases.setdefault(spoken_name, str(Path.home() / "Vaani"))
    want = " ".join(_norm_tokens(spoken))
    for key in (want, want.replace(" ", "")):
        target = aliases.get(key)
        if target:
            path = Path(target).expanduser()
            if path.is_dir():
                return ProjectMatch(path.name, path, 1.0)
    for key, target in aliases.items():
        if difflib.SequenceMatcher(None, want.replace(" ", ""), key.replace(" ", "")).ratio() >= 0.85:
            path = Path(target).expanduser()
            if path.is_dir():
                return ProjectMatch(path.name, path, 0.95)
    return None


_INDEX = ProjectIndex()


def project_index() -> ProjectIndex:
    return _INDEX


def open_in_editor(match: ProjectMatch, editor: Editor, *, popen=subprocess.Popen) -> str:
    exe = next((p for name in editor.executables if (p := shutil.which(name))), None)
    if exe is None:
        return f"{editor.name} isn't installed."
    try:
        popen(
            [exe, str(match.path)],
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return f"Couldn't start {editor.name}."
    LOGGER.info("event=project_open editor=%s path=%s score=%.2f", editor.key, match.path, match.score)
    return f"Opened {match.name} in {editor.name}."
