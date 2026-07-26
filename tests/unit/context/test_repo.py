"""Repo resolution via exec runner (T3.2)."""
from __future__ import annotations

from pathlib import Path

from dataclasses import dataclass

from vaani.context.repo import resolve_repo


@dataclass(frozen=True)
class _Done:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    cancelled: bool = False


def test_resolve_repo_uses_git_rev_parse_argv(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    seen: list[tuple[str, ...]] = []

    def runner(cmd: object) -> _Done:
        argv = getattr(cmd, "argv")
        seen.append(argv)
        if argv[:3] == ("git", "-C", str(root)) or (
            len(argv) >= 3 and argv[0] == "git" and argv[1] == "-C"
        ):
            if "--show-toplevel" in argv:
                return _Done(argv=argv, returncode=0, stdout=f"{root}\n", stderr="")
            if "--abbrev-ref" in argv and "HEAD" in argv:
                return _Done(argv=argv, returncode=0, stdout="feat\n", stderr="")
            if "--porcelain" in argv:
                return _Done(argv=argv, returncode=0, stdout=" M a\n", stderr="")
            if "remote.origin.url" in argv:
                return _Done(
                    argv=argv,
                    returncode=0,
                    stdout="git@github.com:org/repo.git\n",
                    stderr="",
                )
        return _Done(argv=argv, returncode=1, stdout="", stderr="err")

    info = resolve_repo(root, runner=runner)
    assert info is not None
    assert info.root == root
    assert info.branch == "feat"
    assert info.dirty is True
    assert info.remote_host == "github.com"
    assert any("--show-toplevel" in argv for argv in seen)
    assert all(argv[0] == "git" for argv in seen)
    # Never a joined shell string.
    assert all(isinstance(part, str) for argv in seen for part in argv)


def test_resolve_repo_not_a_repo(tmp_path: Path) -> None:
    def runner(cmd: object) -> _Done:
        argv = getattr(cmd, "argv")
        return _Done(argv=argv, returncode=128, stdout="", stderr="not a git repo")

    assert resolve_repo(tmp_path, runner=runner) is None
