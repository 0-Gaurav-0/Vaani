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


def test_resolves_rickroll_play_on_youtube():
    from vaani.sites import resolve_youtube, RICKROLL_URL

    target = resolve_youtube("play Rick and Roll on YouTube")
    assert target is not None
    assert target.url == RICKROLL_URL
    assert "Rickroll" in target.name


def test_resolves_rickroll_despite_whisper_typo():
    from vaani.sites import resolve_youtube, RICKROLL_URL

    # Real log: Whisper heard "reck" instead of "rick".
    target = resolve_youtube("play reck and roll on youtube")
    assert target is not None
    assert target.url == RICKROLL_URL


def test_resolves_youtube_search_play(monkeypatch):
    from vaani.sites import resolve_youtube

    monkeypatch.setattr(
        "vaani.sites._first_youtube_watch_url",
        lambda query, timeout=2.5: f"https://www.youtube.com/watch?v=abcdefghijk",
    )
    target = resolve_youtube("play lo-fi hip hop on youtube")
    assert target is not None
    assert target.url.endswith("watch?v=abcdefghijk")


def test_search_on_youtube_stays_on_results_page(monkeypatch):
    from vaani.sites import resolve_youtube

    monkeypatch.setattr(
        "vaani.sites._first_youtube_watch_url",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not resolve")),
    )
    target = resolve_youtube("search for lo-fi on youtube")
    assert target is not None
    assert "youtube.com/results" in target.url


def test_resolves_hinglish_youtube_pe_chalao(monkeypatch):
    from vaani.sites import resolve_youtube

    seen = []

    def fake_resolve(query, timeout=2.5):
        seen.append(query)
        return "https://www.youtube.com/watch?v=kesariya001"

    monkeypatch.setattr("vaani.sites._first_youtube_watch_url", fake_resolve)
    target = resolve_youtube("youtube pe kesariya gaana chalao")
    assert target is not None
    assert target.url.endswith("watch?v=kesariya001")
    assert seen == ["kesariya"]


def test_resolves_hinglish_song_youtube_pe_play_karo(monkeypatch):
    from vaani.sites import resolve_youtube

    monkeypatch.setattr(
        "vaani.sites._first_youtube_watch_url",
        lambda query, timeout=2.5: f"https://www.youtube.com/watch?v={query.replace(' ', '')[:11].ljust(11, 'x')}",
    )
    target = resolve_youtube("tum hi ho youtube pe play karo")
    assert target is not None
    assert "watch?v=" in target.url


def test_open_youtube_site():
    target = resolve_site("Open YouTube")
    assert target is not None
    assert target.url == "https://www.youtube.com/"


def test_hinglish_kholo_opens_site():
    target = resolve_site("Gmail kholo")
    assert target is not None
    assert target.name == "Gmail"


def test_browse_verbs_and_known_sites():
    assert resolve_site("GitHub pe jao").name == "GitHub"
    assert resolve_site("visit Wikipedia").name == "Wikipedia"
    assert resolve_site("browse LinkedIn").name == "LinkedIn"


def test_resolve_browse_query_domain_and_search():
    from vaani.sites import resolve_browse_query

    domain = resolve_browse_query("github.com")
    assert domain is not None
    assert domain.url.startswith("https://github.com")

    spoken = resolve_browse_query("stackoverflow dot com")
    assert spoken is not None
    assert "stackoverflow.com" in spoken.url

    search = resolve_browse_query("best mechanical keyboards")
    assert search is not None
    assert "google.com/search" in search.url


def test_codex_fast_command_is_ephemeral_and_isolated():
    command = CodexRunner.command_for("codex", "explain this")
    assert "--ephemeral" in command
    assert "--ignore-user-config" in command
    assert 'model_reasoning_effort="low"' in command


def test_bare_play_defaults_to_youtube(monkeypatch):
    from vaani.sites import resolve_youtube

    monkeypatch.setattr(
        "vaani.sites._first_youtube_watch_url",
        lambda query, timeout=2.5: "https://www.youtube.com/watch?v=abcdefghijk",
    )
    target = resolve_youtube("play Kesariya")
    assert target is not None
    assert target.url.endswith("watch?v=abcdefghijk")
    assert "YouTube" in target.name


def test_can_you_play_defaults_to_youtube(monkeypatch):
    from vaani.sites import resolve_youtube

    monkeypatch.setattr(
        "vaani.sites._first_youtube_watch_url",
        lambda query, timeout=2.5: "https://www.youtube.com/watch?v=abcdefghijk",
    )
    for phrase in (
        "Can you play the trailer of Spider-Man brand new day?",
        "could you please play the trailer for Spiderman brand new day",
        "will you play brand new day trailer",
    ):
        target = resolve_youtube(phrase)
        assert target is not None, phrase
        assert target.url.endswith("watch?v=abcdefghijk"), phrase
        assert "YouTube" in target.name


def test_play_on_prime_uses_ott_search():
    from vaani.sites import resolve_youtube

    target = resolve_youtube("play Hellmary on amazon prime")
    assert target is not None
    assert "primevideo.com" in target.url
    assert "Prime Video" in target.name


def test_play_it_alone_is_ignored():
    from vaani.sites import resolve_youtube

    assert resolve_youtube("play it") is None
