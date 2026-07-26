"""Slugification table tests (T4.2 / CLI-GIT-BRANCH-01)."""
from __future__ import annotations

import pytest

from vaani.intent.normalize import normalize
from vaani.intent.slug import (
    BranchConvention,
    infer_branch_convention,
    is_plausible_ref,
    slugify_branch,
)


# ~30 spoken / normalized → branch name cases.
_SLUG_CASES: list[tuple[str, str]] = [
    ("assistant use cases", "assistant-use-cases"),
    ("feature slash port killer", "feature/port-killer"),
    ("linux fix", "linux-fix"),
    ("my underscore branch", "my-branch"),  # default kebab rewrites _
    ("foo dash bar", "foo-bar"),
    ("Foo Bar Baz", "foo-bar-baz"),
    ("ALREADY-kebab", "already-kebab"),
    ("already_snake", "already-snake"),  # default kebab rewrites _
    ("path slash to slash name", "path/to/name"),
    ("a slash b", "a/b"),
    ("hotfix login form", "hotfix-login-form"),
    ("release 2.0", "release-2.0"),
    ("chore update deps", "chore-update-deps"),
    ("bugfix login page", "bugfix-login-page"),
    ("feat slash av pack git", "feat/av-pack-git"),
    ("docs slash readme", "docs/readme"),
    ("UPPER CASE NAME", "upper-case-name"),
    ("  spaced   out  ", "spaced-out"),
    ("single", "single"),
    ("with/slash already", "with/slash-already"),
    ("ends with dash-", "ends-with-dash"),
    ("--leading", "leading"),
    ("multi---dash", "multi-dash"),
    ("name with.dot", "name-with.dot"),
    ("port killer", "port-killer"),
    ("assistant-use-cases", "assistant-use-cases"),
    ("feature/port-killer", "feature/port-killer"),
    ("fix the linux audio bug", "fix-the-linux-audio-bug"),
    ("wip", "wip"),
    ("user slash john slash patch", "user/john/patch"),
]


@pytest.mark.parametrize("spoken,expected", _SLUG_CASES)
def test_slugify_branch_table(spoken: str, expected: str) -> None:
    normalized = normalize(spoken)
    assert slugify_branch(normalized) == expected


def test_snake_convention_from_existing_branches() -> None:
    branches = ("main", "feature_login", "bugfix_audio", "chore_deps")
    assert infer_branch_convention(branches) is BranchConvention.SNAKE
    assert (
        slugify_branch("assistant use cases", branches=branches)
        == "assistant_use_cases"
    )


def test_kebab_wins_when_mixed_or_empty() -> None:
    assert infer_branch_convention(()) is BranchConvention.KEBAB
    assert infer_branch_convention(("main", "master")) is BranchConvention.KEBAB
    mixed = ("feat/foo-bar", "feat/foo_bar", "feat/baz-qux")
    assert infer_branch_convention(mixed) is BranchConvention.KEBAB


@pytest.mark.parametrize(
    "name,ok",
    [
        ("assistant-use-cases", True),
        ("feature/port-killer", True),
        ("", False),
        (".", False),
        ("..", False),
        ("has space", False),
        ("ends.", False),
        ("foo..bar", False),
        ("foo@{bar", False),
        ("foo.lock", False),
        ("/leading", False),
        ("trailing/", False),
    ],
)
def test_is_plausible_ref(name: str, ok: bool) -> None:
    assert is_plausible_ref(name) is ok
