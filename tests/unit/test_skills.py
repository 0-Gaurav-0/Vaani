from pathlib import Path

from vaani.skills import load_skill_index, match_skill, parse_skill_file

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "skills"


def test_parse_skill_without_mcps_defaults_empty():
    skill = parse_skill_file(FIXTURES / "sample-skill" / "SKILL.md")
    assert skill is not None
    assert skill.id == "sample-skill"
    assert skill.name == "sample-skill"
    assert "sample" in skill.aliases
    assert skill.mcps == ()
    assert "Sample Skill" in skill.body()


def test_parse_skill_with_mcps():
    skill = parse_skill_file(FIXTURES / "with-mcps" / "SKILL.md")
    assert skill is not None
    assert skill.mcps == ("browseros",)
    assert "browser workflow" in skill.aliases


def test_load_skill_index_scans_roots_and_skips_empty_dirs(tmp_path):
    empty = tmp_path / "empty-dir"
    empty.mkdir()
    roots = [FIXTURES, tmp_path]
    skills = load_skill_index(roots)
    ids = {s.id for s in skills}
    assert ids == {"sample-skill", "with-mcps"}


def test_match_explicit_run_skill_phrase():
    skills = load_skill_index([FIXTURES])
    hit = match_skill("run the sample skill for today's report", skills)
    assert hit is not None
    assert hit.id == "sample-skill"


def test_match_explicit_use_skill_with_alias():
    skills = load_skill_index([FIXTURES])
    hit = match_skill("use the browser workflow skill please", skills)
    assert hit is not None
    assert hit.id == "with-mcps"


def test_match_high_confidence_name_in_utterance():
    skills = load_skill_index([FIXTURES])
    hit = match_skill("please run sample-skill on the pricing sheet", skills)
    assert hit is not None
    assert hit.id == "sample-skill"


def test_match_ambiguous_or_unknown_returns_none():
    skills = load_skill_index([FIXTURES])
    assert match_skill("do something useful", skills) is None
    assert match_skill("run the unknown skill now", skills) is None
