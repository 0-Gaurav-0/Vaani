"""Unit tests for the forge / gh pack (T4.3)."""
from __future__ import annotations

import json
from pathlib import Path

from vaani.intent.grammar import match
from vaani.intent.schema import Context, Intent, RepoInfo, Status, Support
from vaani.platform.protocol import PlatformId
from vaani.policy.dryrun import materialize_argv
from vaani.verbs.packs.forge import (
    FORGE_VERB_NAMES,
    build_forge_verbs,
    forge_patterns,
    materialize_forge_argv,
)
from vaani.verbs.packs.registry import PackRegistry
from vaani.verbs.registry import Registry


def _intent(
    verb: str,
    slots: dict | None = None,
    *,
    modifiers: frozenset[str] = frozenset(),
) -> Intent:
    return Intent(
        verb=verb,
        slots=slots or {},
        rung=4,
        confidence=1.0,
        source="test",
        mode="act",
        utterance=verb,
        raw_utterance=verb,
        modifiers=modifiers,
        brain=None,
    )


def _context(root: Path | None = None) -> Context:
    repo = None
    if root is not None:
        repo = RepoInfo(root=root, branch="feature/x", dirty=False)
    return Context(
        platform=PlatformId.LINUX,
        workspace=root,
        workspace_source="test",
        repo=repo,
        project=None,
        focus=None,
        screen=None,
        session=None,
    )


class Done:
    def __init__(self, code: int = 0, stdout: str = "", stderr: str = ""):
        self.returncode = code
        self.stdout = stdout
        self.stderr = stderr
        self.timed_out = False
        self.cancelled = False
        self.argv: tuple[str, ...] = ()


def _verbs(**kwargs):
    return {v.name: v for v in build_forge_verbs(**kwargs)}


def test_forge_verb_names() -> None:
    assert set(_verbs(which=lambda b: f"/bin/{b}")) == FORGE_VERB_NAMES


def test_patterns_cover_canonical_utterances() -> None:
    patterns = forge_patterns()
    assert match("create a pull request", patterns)[0] == "forge.pr.create"
    assert match("open the pr i was working on", patterns)[0] == "forge.pr.open"
    assert match("list my open prs", patterns)[0] == "forge.pr.list"
    hit = match("check out the pr branch for pull request 3", patterns)
    assert hit is not None
    assert hit[0] == "forge.pr.checkout"
    assert hit[1].get("number") in {"3", 3}
    assert match("merge the pull request", patterns)[0] == "forge.pr.merge"
    assert match("login to gh", patterns)[0] == "forge.auth.login"


def test_materialize_argv_dryrun_shapes() -> None:
    ctx = _context(Path("/tmp/repo"))
    for verb in sorted(FORGE_VERB_NAMES):
        slots: dict = {}
        if verb in {"forge.pr.checkout", "forge.pr.merge"}:
            slots = {"number": 3}
        if verb == "forge.pr.merge":
            slots["strategy"] = "squash"
        argv = materialize_argv(
            verb, slots, platform=PlatformId.LINUX, context=ctx
        )
        assert argv is not None, verb
        assert argv[0] == "gh", verb
        direct = materialize_forge_argv(verb, slots, context=ctx)
        assert direct == argv


def test_pr_create_needs_confirm_shows_title_base(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()

    def run_fn(cmd):
        argv = tuple(cmd.argv)
        if argv[:3] == ("gh", "auth", "status"):
            return Done(0)
        if argv[:3] == ("gh", "repo", "view"):
            if "defaultBranchRef" in argv:
                return Done(
                    0,
                    json.dumps({"defaultBranchRef": {"name": "main"}}),
                )
            return Done(0, json.dumps({"nameWithOwner": "acme/vaani"}))
        if argv[:3] == ("git", "log", "-1"):
            return Done(0, "Add forge pack\n")
        if argv[:3] == ("git", "rev-parse"):
            return Done(0, "feature/x\n")
        if argv[:3] == ("gh", "pr", "create"):
            return Done(0, "https://github.com/acme/vaani/pull/9\n")
        return Done(0)

    verbs = _verbs(run_fn=run_fn, which=lambda b: f"/bin/{b}")
    result = verbs["forge.pr.create"].handler(
        _intent("forge.pr.create"),
        _context(root),
    )
    assert result.status is Status.NEEDS_CONFIRM
    assert result.pending is not None
    assert result.pending.risk.value == "R2"
    assert "Add forge pack" in result.detail
    assert "base=main" in result.detail
    assert result.evidence == ("gh", "pr", "create", "--fill")


def test_pr_merge_r3_shows_repo_number_title_strategy(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    prs = [{"number": 7, "title": "Ship forge", "url": "https://example/7"}]

    def run_fn(cmd):
        argv = tuple(cmd.argv)
        if argv[:3] == ("gh", "auth", "status"):
            return Done(0)
        if argv[:3] == ("gh", "pr", "list"):
            return Done(0, json.dumps(prs))
        if argv[:3] == ("gh", "repo", "view"):
            return Done(0, json.dumps({"nameWithOwner": "acme/vaani"}))
        return Done(0)

    verbs = _verbs(run_fn=run_fn, which=lambda b: f"/bin/{b}")
    result = verbs["forge.pr.merge"].handler(
        _intent("forge.pr.merge", {"strategy": "squash"}),
        _context(root),
    )
    assert result.status is Status.NEEDS_CONFIRM
    assert result.pending is not None
    assert result.pending.risk.value == "R3"
    assert "repo=acme/vaani" in result.detail
    assert "number=7" in result.detail
    assert "Ship forge" in result.detail
    assert "strategy=squash" in result.detail
    assert result.evidence == ("gh", "pr", "merge", "7", "--squash")


def test_pr_merge_confirmed_runs(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    calls: list[tuple[str, ...]] = []

    def run_fn(cmd):
        argv = tuple(cmd.argv)
        calls.append(argv)
        if argv[:3] == ("gh", "auth", "status"):
            return Done(0)
        if argv[:3] == ("gh", "pr", "view"):
            return Done(
                0,
                json.dumps({"number": 3, "title": "Checkout me"}),
            )
        if argv[:3] == ("gh", "repo", "view"):
            return Done(0, json.dumps({"nameWithOwner": "acme/vaani"}))
        return Done(0)

    verbs = _verbs(run_fn=run_fn, which=lambda b: f"/bin/{b}")
    result = verbs["forge.pr.merge"].handler(
        _intent(
            "forge.pr.merge",
            {"number": 3, "strategy": "merge"},
            modifiers=frozenset({"confirmed"}),
        ),
        _context(root),
    )
    assert result.status is Status.OK
    assert ("gh", "pr", "merge", "3", "--merge") in calls


def test_multiple_prs_needs_disambiguate(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    prs = [
        {"number": 1, "title": "One"},
        {"number": 2, "title": "Two"},
    ]

    def run_fn(cmd):
        argv = tuple(cmd.argv)
        if argv[:3] == ("gh", "auth", "status"):
            return Done(0)
        if argv[:3] == ("gh", "pr", "list"):
            return Done(0, json.dumps(prs))
        return Done(0)

    verbs = _verbs(run_fn=run_fn, which=lambda b: f"/bin/{b}")
    result = verbs["forge.pr.open"].handler(
        _intent("forge.pr.open"),
        _context(root),
    )
    assert result.status is Status.NEEDS_DISAMBIGUATE
    assert result.disambiguation is not None
    assert len(result.disambiguation.options) == 2
    assert result.disambiguation.options[0].label.startswith("#1")


def test_disambiguate_hook_selects_pr(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    prs = [
        {"number": 1, "title": "One"},
        {"number": 2, "title": "Two"},
    ]
    calls: list[tuple[str, ...]] = []

    def run_fn(cmd):
        argv = tuple(cmd.argv)
        calls.append(argv)
        if argv[:3] == ("gh", "auth", "status"):
            return Done(0)
        if argv[:3] == ("gh", "pr", "list"):
            return Done(0, json.dumps(prs))
        return Done(0)

    verbs = _verbs(
        run_fn=run_fn,
        which=lambda b: f"/bin/{b}",
        disambiguate=lambda _opts, _prompt: "2",
    )
    result = verbs["forge.pr.open"].handler(
        _intent("forge.pr.open"),
        _context(root),
    )
    assert result.status is Status.OK
    assert ("gh", "pr", "view", "2", "--web") in calls


def test_auth_not_logged_in_degraded(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()

    def run_fn(cmd):
        if tuple(cmd.argv)[:3] == ("gh", "auth", "status"):
            return Done(1, "", "not logged in")
        return Done(0)

    verbs = _verbs(run_fn=run_fn, which=lambda b: f"/bin/{b}")
    result = verbs["forge.pr.create"].handler(
        _intent("forge.pr.create"),
        _context(root),
    )
    assert result.status is Status.UNSUPPORTED
    assert "gh isn't logged in" in result.summary


def test_auth_login_skips_auth_precondition(tmp_path: Path) -> None:
    calls: list[tuple[str, ...]] = []

    def run_fn(cmd):
        argv = tuple(cmd.argv)
        calls.append(argv)
        if argv[:3] == ("gh", "auth", "status"):
            return Done(1, "", "not logged in")
        return Done(0)

    verbs = _verbs(run_fn=run_fn, which=lambda b: f"/bin/{b}")
    result = verbs["forge.auth.login"].handler(
        _intent("forge.auth.login"),
        _context(tmp_path),
    )
    assert result.status is Status.OK
    assert ("gh", "auth", "login") in calls
    assert not any(a[:3] == ("gh", "auth", "status") for a in calls)


def test_missing_gh_degraded_matrix_and_handler(tmp_path: Path) -> None:
    packs = PackRegistry(tmp_path / "packs.json", which=lambda _b: None)
    packs.set_enabled("forge", True)
    registry = Registry()
    for verb in build_forge_verbs(which=lambda _b: None):
        registry.register(verb)
    packs.apply(registry)
    support, note = registry.matrix()["forge.pr.create"]["linux"]
    assert support is Support.DEGRADED
    assert note == "gh isn't installed"

    verbs = _verbs(which=lambda _b: None)
    result = verbs["forge.pr.list"].handler(
        _intent("forge.pr.list"),
        _context(tmp_path),
    )
    assert result.status is Status.UNSUPPORTED
    assert "gh isn't installed" in result.summary


def test_require_binary_via_packs(tmp_path: Path) -> None:
    packs = PackRegistry(
        tmp_path / "packs.json",
        which=lambda name: "/usr/bin/gh" if name == "gh" else None,
    )
    packs.set_enabled("forge", True)

    def run_fn(cmd):
        if tuple(cmd.argv)[:3] == ("gh", "auth", "status"):
            return Done(0)
        if tuple(cmd.argv)[:3] == ("gh", "pr", "list"):
            return Done(0, "")
        return Done(0)

    verbs = _verbs(run_fn=run_fn, packs=packs)
    result = verbs["forge.pr.list"].handler(
        _intent("forge.pr.list"),
        _context(tmp_path),
    )
    assert result.status is Status.OK


def test_pack_disabled_unsupported(tmp_path: Path) -> None:
    packs = PackRegistry(tmp_path / "packs.json", which=lambda b: f"/bin/{b}")
    assert packs.is_enabled("forge") is False
    registry = Registry()
    for verb in build_forge_verbs(which=lambda b: f"/bin/{b}"):
        registry.register(verb)
    packs.apply(registry)
    support, note = registry.matrix()["forge.pr.create"]["linux"]
    assert support is Support.UNSUPPORTED
    assert note == "pack disabled"


def test_pr_checkout_runs(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    calls: list[tuple[str, ...]] = []

    def run_fn(cmd):
        argv = tuple(cmd.argv)
        calls.append(argv)
        if argv[:3] == ("gh", "auth", "status"):
            return Done(0)
        return Done(0)

    verbs = _verbs(run_fn=run_fn, which=lambda b: f"/bin/{b}")
    result = verbs["forge.pr.checkout"].handler(
        _intent("forge.pr.checkout", {"number": 3}),
        _context(root),
    )
    assert result.status is Status.OK
    assert ("gh", "pr", "checkout", "3") in calls
