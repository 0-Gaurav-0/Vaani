import json

from vaani.sites import resolve_site
from vaani.codex import CodexRunner


def test_resolves_local_site_and_browser(tmp_path):
    config = tmp_path / "sites.json"
    config.write_text(json.dumps([
        {
            "aliases": ["project dashboard", "dashboard"],
            "name": "Project Dashboard",
            "url": "https://example.com/dashboard",
        }
    ]))
    target = resolve_site("Open the project dashboard in Brave", config_path=config)
    assert target is not None
    assert target.name == "Project Dashboard"
    assert target.browser == "brave"
    assert target.url == "https://example.com/dashboard"


def test_invalid_local_config_is_ignored(tmp_path):
    config = tmp_path / "sites.json"
    config.write_text('[{"aliases":["files"],"name":"Files","url":"file:///tmp"}]')
    assert resolve_site("Open files", config_path=config) is None


def test_resolves_stripe_to_stable_dashboard_url():
    target = resolve_site("Open Stripe")
    assert target is not None
    assert target.name == "Stripe"
    assert target.url == "https://dashboard.stripe.com/"


def test_claude_website_can_be_requested_explicitly():
    target = resolve_site("Open Claude website in Brave")
    assert target is not None
    assert target.name == "Claude"
    assert target.browser == "brave"


def test_does_not_route_non_action_mentions():
    assert resolve_site("Explain how Stripe works") is None


def test_codex_fast_command_is_ephemeral_and_isolated():
    command = CodexRunner.command_for("codex", "explain this")
    assert "--ephemeral" in command
    assert "--ignore-user-config" in command
    assert 'model_reasoning_effort="low"' in command
