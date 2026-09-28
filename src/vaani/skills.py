"""Lightweight Agent Skill index and matcher for Vaani assistant routing.

Index/match read SKILL.md metadata only — never start MCP servers or Codex.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class SkillMeta:
    id: str
    name: str
    description: str
    path: Path
    aliases: tuple[str, ...] = ()
    mcps: tuple[str, ...] = ()

    def body(self) -> str:
        text = self.path.read_text(encoding="utf-8")
        if text.startswith("---"):
            end = text.find("\n---", 3)
            if end != -1:
                return text[end + 4 :].lstrip("\n")
        return text


def default_skill_roots() -> list[Path]:
    home = Path.home()
    return [home / ".agents" / "skills", home / ".claude" / "skills"]


def _parse_frontmatter(text: str) -> tuple[dict[str, object], str]:
    """Parse a minimal YAML-like frontmatter block without PyYAML."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    block = text[3:end].strip("\n")
    body = text[end + 4 :].lstrip("\n")
    meta: dict[str, object] = {}
    current_list: str | None = None
    for raw in block.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.strip().startswith("#"):
            continue
        if current_list and re.match(r"^\s+-\s+", line):
            item = re.sub(r"^\s+-\s+", "", line).strip().strip("'\"")
            values = meta.setdefault(current_list, [])
            assert isinstance(values, list)
            values.append(item)
            continue
        current_list = None
        m = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if not m:
            continue
        key, value = m.group(1), m.group(2).strip()
        if value == "" or value in {"[]", "{}"}:
            if value == "[]":
                meta[key] = []
            else:
                current_list = key
                meta[key] = []
            continue
        meta[key] = value.strip("'\"").strip()
    return meta, body


def parse_skill_file(path: Path) -> SkillMeta | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    meta, _body = _parse_frontmatter(text)
    skill_id = path.parent.name
    name = str(meta.get("name") or skill_id)
    description = str(meta.get("description") or "")
    aliases_raw = meta.get("aliases") or []
    mcps_raw = meta.get("mcps") or []
    aliases = tuple(
        str(a).casefold().strip()
        for a in (aliases_raw if isinstance(aliases_raw, list) else [])
        if str(a).strip()
    )
    mcps = tuple(
        str(m).strip()
        for m in (mcps_raw if isinstance(mcps_raw, list) else [])
        if str(m).strip()
    )
    return SkillMeta(
        id=skill_id,
        name=name,
        description=description,
        path=path,
        aliases=aliases,
        mcps=mcps,
    )


def load_skill_index(roots: list[Path] | None = None) -> list[SkillMeta]:
    roots = roots if roots is not None else default_skill_roots()
    skills: list[SkillMeta] = []
    seen: set[str] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            skill_path = child / "SKILL.md"
            if not skill_path.is_file():
                continue
            skill = parse_skill_file(skill_path)
            if skill is None or skill.id in seen:
                continue
            seen.add(skill.id)
            skills.append(skill)
    return skills


def _normalize(text: str) -> str:
    return " ".join(text.casefold().strip().split())


def _explicit_skill_name(utterance: str) -> str | None:
    normalized = _normalize(utterance)
    patterns = (
        r"\b(?:run|use|execute|start)\s+(?:the\s+)?(.+?)\s+skill\b",
        r"\bwith\s+(?:the\s+)?(.+?)\s+skill\b",
    )
    for pattern in patterns:
        m = re.search(pattern, normalized)
        if m:
            name = m.group(1).strip(" .,!?\"'")
            name = re.sub(r"^(my|the|a|an)\s+", "", name).strip()
            return name or None
    return None


def _labels(skill: SkillMeta) -> set[str]:
    labels = {_normalize(skill.id), _normalize(skill.name), *{_normalize(a) for a in skill.aliases}}
    # Allow "sample skill" to match id "sample-skill"
    labels.add(_normalize(skill.id.replace("-", " ")))
    labels.add(_normalize(skill.name.replace("-", " ")))
    return {label for label in labels if label}


def match_skill(utterance: str, skills: list[SkillMeta]) -> SkillMeta | None:
    if not skills:
        return None
    normalized = _normalize(utterance)
    explicit = _explicit_skill_name(utterance)
    if explicit:
        hits = [s for s in skills if explicit in _labels(s) or any(explicit == lab for lab in _labels(s))]
        # Also allow substring when user says partial explicit name
        if not hits:
            hits = [
                s
                for s in skills
                if any(explicit == lab or lab.startswith(explicit) or explicit.startswith(lab) for lab in _labels(s))
            ]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            return None
        return None

    # High-confidence: a unique skill label appears as a phrase in the utterance.
    hits = []
    for skill in skills:
        if any(
            re.search(rf"(?<!\w){re.escape(lab)}(?!\w)", normalized)
            for lab in _labels(skill)
            if len(lab) >= 4
        ):
            hits.append(skill)
    if len(hits) == 1:
        return hits[0]
    return None
