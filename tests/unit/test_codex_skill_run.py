from pathlib import Path

from vaani.codex import (
    CodexRunner,
    build_skill_prompt,
    extract_mcp_server_blocks,
    prepare_skill_codex_home,
)


def test_chat_command_still_ignores_user_config():
    cmd = CodexRunner.command_for("codex", "hello")
    assert "--ignore-user-config" in cmd
    assert "saleshandy" not in " ".join(cmd)


def test_skill_command_has_no_unrelated_mcp_names():
    prompt = build_skill_prompt("# Skill\nDo the thing.", "run it")
    cmd = CodexRunner.command_for_skill("codex", prompt, mcp_names=["browseros"])
    joined = " ".join(cmd)
    assert "saleshandy" not in joined
    assert "mixpanel" not in joined
    assert "--ignore-user-config" not in cmd  # temp CODEX_HOME instead
    assert "Do the thing." in prompt and "run it" in prompt


def test_extract_mcp_server_blocks_allowlists_only():
    config = """
[mcp_servers.browseros]
url = "http://127.0.0.1:9200/mcp"

[mcp_servers.saleshandy]
url = "https://example.com/mcp"

[mcp_servers.metabase]
command = "uv"

[mcp_servers.metabase.env]
LOG_LEVEL = "INFO"
"""
    block = extract_mcp_server_blocks(config, ["browseros", "metabase"])
    assert "browseros" in block
    assert "metabase" in block
    assert "metabase.env" in block
    assert "saleshandy" not in block


def test_prepare_skill_codex_home_empty_mcps(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "auth.json").write_text('{"token":"x"}', encoding="utf-8")
    (source / "config.toml").write_text(
        '[mcp_servers.saleshandy]\nurl = "https://example.com"\n',
        encoding="utf-8",
    )
    home = prepare_skill_codex_home([], source_home=source)
    try:
        cfg = (home / "config.toml").read_text(encoding="utf-8")
        assert "saleshandy" not in cfg
        assert (home / "auth.json").exists()
    finally:
        import shutil

        shutil.rmtree(home, ignore_errors=True)


def test_prepare_skill_codex_home_copies_allowlisted_mcp(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "auth.json").write_text('{"token":"x"}', encoding="utf-8")
    (source / "config.toml").write_text(
        '[mcp_servers.browseros]\nurl = "http://127.0.0.1:9200/mcp"\n\n'
        '[mcp_servers.saleshandy]\nurl = "https://example.com"\n',
        encoding="utf-8",
    )
    home = prepare_skill_codex_home(["browseros"], source_home=source)
    try:
        cfg = (home / "config.toml").read_text(encoding="utf-8")
        assert "browseros" in cfg
        assert "saleshandy" not in cfg
    finally:
        import shutil

        shutil.rmtree(home, ignore_errors=True)
