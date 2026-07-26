"""Normalization + lexicon table tests (T1.1)."""
from __future__ import annotations

import json

import pytest

from vaani.intent.lexicon import Lexicon
from vaani.intent.normalize import normalize, port_slot


# Spoken → normalized matching copy. Keep ~60 rows covering numbers, separators,
# fillers, tech lexicon, and SYS-PORT-01 ASR pitfalls.
_NORMALIZE_CASES: list[tuple[str, str]] = [
    # baseline
    ("Open Chrome", "open chrome"),
    ("  lots   of   space  ", "lots of space"),
    ("", ""),
    # fillers (matching copy only)
    ("umm free port 3000", "free port 3000"),
    ("um please open chrome", "open chrome"),
    ("uh can you open chrome", "open chrome"),
    ("can you please free port 8080", "free port 8080"),
    ("could you launch browser", "launch browser"),
    ("would you open a browser", "open a browser"),
    ("please set volume to 30", "set volume to 30"),
    ("hmm open brave", "open brave"),
    ("ah open the browser", "open the browser"),
    # number words → digits
    ("three", "3"),
    ("twelve", "12"),
    ("twenty three", "23"),
    ("thirty", "30"),
    ("forty two", "42"),
    ("ninety nine", "99"),
    ("one hundred", "100"),
    ("one hundred five", "105"),
    ("three hundred", "300"),
    ("three thousand", "3000"),
    ("five thousand", "5000"),
    ("eight thousand", "8000"),
    ("two thousand twenty four", "2024"),
    ("one million", "1000000"),
    # digit-by-digit
    ("three zero zero zero", "3000"),
    ("port three zero zero zero", "port 3000"),
    ("eight zero eight zero", "8080"),
    ("one two three four", "1234"),
    ("oh eight zero eight", "0808"),
    # paired tens (ports)
    ("eighty eighty", "8080"),
    ("free port eighty eighty", "free port 8080"),
    ("kill whatever is on port three thousand", "kill whatever is on port 3000"),
    ("free port three thousand", "free port 3000"),
    ("thirty percent", "30"),
    ("set volume to thirty percent", "set volume to 30"),
    ("volume fifty percent", "volume 50"),
    ("twenty percent", "20"),
    # separators
    ("feature slash port killer", "feature/port killer"),
    ("my underscore file", "my_file"),
    ("foo dash bar", "foo-bar"),
    ("foo hyphen bar", "foo-bar"),
    ("example dot com", "example.com"),
    ("config period json", "config.json"),
    ("path slash to slash file", "path/to/file"),
    ("name underscore with dash value", "name_with-value"),
    ("a slash b slash c", "a/b/c"),
    # tech lexicon (builtin)
    ("run pie test", "run pytest"),
    ("run py test", "run pytest"),
    ("install node package manager", "install npm"),
    ("use p n p m", "use pnpm"),
    ("call kube control", "call kubectl"),
    ("restart engine x", "restart nginx"),
    ("open vise code", "open vscode"),
    ("open vs code", "open vscode"),
    ("use git hub cli", "use gh"),
    # combined
    ("umm can you free port three thousand", "free port 3000"),
    ("please run pie test", "run pytest"),
    ("open feature slash port killer", "open feature/port killer"),
    ("can you set volume to thirty percent", "set volume to 30"),
    ("kill whatever is on port eighty eighty", "kill whatever is on port 8080"),
    # already-normalized stays stable
    ("free port 3000", "free port 3000"),
    ("open localhost 3000", "open localhost 3000"),
    ("pytest", "pytest"),
    ("npm install", "npm install"),
]


@pytest.mark.parametrize(("spoken", "expected"), _NORMALIZE_CASES)
def test_normalize_table(spoken: str, expected: str) -> None:
    assert normalize(spoken) == expected


@pytest.mark.parametrize(("spoken", "expected"), _NORMALIZE_CASES)
def test_normalize_idempotent(spoken: str, expected: str) -> None:
    once = normalize(spoken)
    assert normalize(once) == once
    assert once == expected


def test_raw_transcript_not_mutated_by_normalize() -> None:
    raw = "Umm can you free port three thousand"
    matching = normalize(raw)
    assert raw == "Umm can you free port three thousand"
    assert matching == "free port 3000"


def test_open_localhost_3000_not_bare_port_slot() -> None:
    """SYS-PORT-01 must-NOT-match: host open ≠ free-port."""
    spoken = "open localhost 3000"
    assert normalize(spoken) == "open localhost 3000"
    assert port_slot(spoken) is None
    assert port_slot("Open Localhost 3000") is None
    assert port_slot("go to localhost 8080") is None


@pytest.mark.parametrize(
    ("spoken", "port"),
    [
        ("free port 3000", 3000),
        ("free port three thousand", 3000),
        ("kill whatever is on port eighty eighty", 8080),
        ("port three zero zero zero", 3000),
        ("3000", 3000),
        ("8080", 8080),
    ],
)
def test_port_slot_positive(spoken: str, port: int) -> None:
    assert port_slot(spoken) == port


def test_user_vocab_file_overrides_and_extends(tmp_path) -> None:
    path = tmp_path / "vocab.json"
    path.write_text(
        json.dumps({"replacements": {"acme widget": "acme-widget", "pie test": "custom-pytest"}}),
        encoding="utf-8",
    )
    lex = Lexicon.for_matching(path)
    assert normalize("deploy acme widget", lexicon=lex) == "deploy acme-widget"
    # User override wins over builtin pie test → pytest
    assert normalize("run pie test", lexicon=lex) == "run custom-pytest"


def test_user_vocab_flat_and_list_shapes(tmp_path) -> None:
    flat = tmp_path / "flat.json"
    flat.write_text(json.dumps({"vaani project": "vaani"}), encoding="utf-8")
    assert normalize("open vaani project", lexicon=Lexicon.load(flat)) == "open vaani"

    listed = tmp_path / "list.json"
    listed.write_text(json.dumps(["Zephyr"]), encoding="utf-8")
    # Identity entries load without error; matching still casefolds.
    lex = Lexicon.load(listed)
    assert ("zephyr", "zephyr") in lex.replacements


def test_missing_vocab_file_is_empty(tmp_path) -> None:
    assert Lexicon.load(tmp_path / "missing.json").replacements == ()


def test_router_uses_normalize_for_utterance_field() -> None:
    from vaani.apps import launch_app, resolve_app
    from vaani.intent.router import Router
    from vaani.platform.protocol import PlatformId
    from vaani.sites import resolve_site
    from vaani.verbs.packs.core import build_core_registry

    registry, patterns = build_core_registry(
        resolve_app_fn=resolve_app,
        launch_app_fn=launch_app,
        resolve_site_fn=resolve_site,
        open_browser_fn=lambda **_k: "Opened browser.",
    )
    router = Router(registry, patterns, resolve_app=resolve_app, resolve_site=resolve_site)
    intent = router.route("umm can you open a browser", platform=PlatformId.LINUX)
    assert intent is not None
    assert intent.raw_utterance == "umm can you open a browser"
    assert intent.utterance == "open a browser"
    assert intent.verb == "browser.open"
