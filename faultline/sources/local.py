"""Read repositories that are already on disk.

This is the most common real case — "I have the code, analyse it" — and it needs
no network, no credentials, and no rate limit. It is also what makes the tool
testable without mocking a remote.

A path may be a single repository or a directory containing several. `fetch` is
deliberately a no-op: copying tens of megabytes to analyse them in place would
be waste, so the returned path points at the original.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from . import Commit, FetchResult, RepoRef, SourceError, register

# markers that mean "this directory is the root of a project"
REPO_MARKERS = (
    ".git", "package.json", "pyproject.toml", "setup.py", "Cargo.toml",
    "go.mod", "pom.xml", "build.gradle", "build.gradle.kts", "Gemfile",
    "composer.json", "requirements.txt",
)


def looks_like_repo(path: Path) -> bool:
    return any((path / m).exists() for m in REPO_MARKERS)


@register("local")
class LocalSource:
    name = "local"
    authenticated = True          # nothing to authenticate; no network is used

    def __init__(self, root: str | Path, **_):
        self.root = Path(root).expanduser().resolve()
        if not self.root.is_dir():
            raise SourceError(f"{self.root} is not a directory")
        self._repos: list[RepoRef] | None = None

    # ------------------------------------------------------------------ repos

    def list_repos(self) -> list[RepoRef]:
        if self._repos is not None:
            return self._repos

        if looks_like_repo(self.root):
            self._repos = [self._describe(self.root)]
            return self._repos

        found = []
        for child in sorted(self.root.iterdir()):
            if child.is_dir() and not child.name.startswith(".") and looks_like_repo(child):
                found.append(self._describe(child))
        if not found:
            raise SourceError(
                f"No repositories found under {self.root}. A directory is treated "
                f"as a repository if it contains one of: {', '.join(REPO_MARKERS[:6])}.")
        self._repos = found
        return self._repos

    def _describe(self, path: Path) -> RepoRef:
        size_kb = sum(f.stat().st_size for f in path.rglob("*")
                      if f.is_file()) // 1024
        return RepoRef(
            id=path.name,
            name=path.name,
            default_branch=self._branch(path),
            size_kb=size_kb,
            description=None,
            language=None,
            archived=False,
        )

    def _branch(self, path: Path) -> str:
        try:
            out = subprocess.run(
                ["git", "-C", str(path), "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True, text=True, timeout=10)
            if out.returncode == 0:
                return out.stdout.strip() or "HEAD"
        except Exception:
            pass
        return "HEAD"

    def path_of(self, repo: RepoRef) -> Path:
        if self.root.name == repo.id and looks_like_repo(self.root):
            return self.root
        return self.root / repo.id

    # ---------------------------------------------------------------- fetching

    def fetch(self, repo: RepoRef, dest: Path) -> FetchResult:
        """In-place: nothing is copied. `dest` is ignored on purpose."""
        path = self.path_of(repo)
        if not path.is_dir():
            raise SourceError(f"{path} does not exist")
        files = sum(1 for f in path.rglob("*") if f.is_file())
        size = sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
        return FetchResult(
            repo=repo.id, ref=repo.default_branch,
            commit_sha=self._head(path),
            files=files, bytes=size, path=path, complete=True,
            notes=["local source: analysed in place, nothing copied"],
        )

    def _head(self, path: Path) -> str | None:
        try:
            out = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                                 capture_output=True, text=True, timeout=10)
            return out.stdout.strip() if out.returncode == 0 else None
        except Exception:
            return None

    def head_sha(self, repo: RepoRef) -> str | None:
        return self._head(self.path_of(repo))

    # ----------------------------------------------------------------- history

    def commits(self, repo: RepoRef, limit: int = 100) -> list[Commit]:
        path = self.path_of(repo)
        try:
            out = subprocess.run(
                ["git", "-C", str(path), "log", f"-{limit}",
                 "--pretty=format:%H\x1f%aI\x1f%an\x1f%s"],
                capture_output=True, text=True, timeout=60)
        except Exception:
            return []
        if out.returncode != 0:
            return []
        commits = []
        for line in out.stdout.splitlines():
            parts = line.split("\x1f")
            if len(parts) == 4:
                commits.append(Commit(sha=parts[0], date=parts[1],
                                      author=parts[2], message=parts[3]))
        return commits
