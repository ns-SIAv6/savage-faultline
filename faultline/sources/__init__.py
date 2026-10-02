"""Where repositories come from.

A `Source` answers three questions and nothing else: what repositories exist,
give me one, and give me its history. Everything above this layer works off
files on disk, so analysis never needs to know whether the code arrived from
GitHub, GitLab, or a directory the user already had.

Credential-free operation is a first-class mode, not a degraded one. Public
repositories must work with no token at all.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

__all__ = [
    "RepoRef", "FetchResult", "Commit", "Source", "SourceError",
    "RateLimited", "open_source", "register", "available_sources",
]


class SourceError(RuntimeError):
    """Anything that stops us reading a repository."""


class RateLimited(SourceError):
    """The host refused more requests.

    Raised with a human-readable remedy, because the difference between 60 and
    5,000 requests an hour is the difference between a tool that works and one
    that appears to hang.
    """

    def __init__(self, message: str, retry_after: int | None = None):
        super().__init__(message)
        self.retry_after = retry_after


@dataclass(frozen=True)
class RepoRef:
    """A repository, identified well enough to fetch and re-fetch."""
    id: str                    # unique within its source, e.g. "example-org/example-repo"
    name: str                  # short name used in reports
    default_branch: str = "main"
    size_kb: int = 0           # host's own size figure; 0 means "unknown or empty"
    description: str | None = None
    language: str | None = None
    archived: bool = False

    @property
    def is_empty(self) -> bool:
        return self.size_kb == 0


@dataclass
class FetchResult:
    """What a fetch actually produced, so the model can record real numbers."""
    repo: str
    ref: str
    commit_sha: str | None
    files: int = 0
    bytes: int = 0
    skipped: int = 0
    path: Path | None = None
    complete: bool = True       # False if we hit a limit or a partial failure
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Commit:
    sha: str
    date: str                  # ISO-8601
    message: str
    author: str | None = None


@runtime_checkable
class Source(Protocol):
    """Read-only access to a set of repositories."""

    name: str
    authenticated: bool

    def list_repos(self) -> list[RepoRef]:
        """Every repository this source can see."""

    def fetch(self, repo: RepoRef, dest: Path) -> FetchResult:
        """Materialise a repository under `dest`. Must prefer one request per
        repository over one request per file — the request budget is the
        binding constraint, not bandwidth."""

    def commits(self, repo: RepoRef, limit: int = 100) -> list[Commit]:
        """Recent history, newest first."""


_REGISTRY: dict[str, type] = {}


def register(scheme: str):
    def deco(cls):
        _REGISTRY[scheme] = cls
        return cls
    return deco


def available_sources() -> list[str]:
    return sorted(_REGISTRY)


def open_source(target: str, **kwargs) -> Source:
    """Turn a user-supplied string into a Source.

    This is the zero-configuration entry point:

        open_source("./my-project")      -> local directory
        open_source("owner/repo")        -> GitHub
        open_source("https://github.com/owner/repo")
        open_source("https://gitlab.com/owner/repo")

    A path that exists on disk always wins, so `./owner/repo` is never mistaken
    for a remote.
    """
    from . import local, github  # noqa: F401  (registers adapters)

    if os.path.exists(target):
        return _REGISTRY["local"](target, **kwargs)

    if target.startswith(("http://", "https://")):
        host = target.split("//", 1)[1].split("/", 1)[0].lower()
        if "gitlab" in host:
            raise SourceError(
                "GitLab is not implemented yet. Pass a local clone, or a GitHub "
                "URL. The Source interface exists so this is an adapter, not a "
                "rewrite.")
        return _REGISTRY["github"](target, **kwargs)

    if re.fullmatch(r"[A-Za-z0-9][\w.-]*(/[A-Za-z0-9][\w.-]*)?", target):
        # "owner" or "owner/repo" — an org or user name, or one repository
        return _REGISTRY["github"](target, **kwargs)

    raise SourceError(
        f"Cannot interpret {target!r}. Give a path that exists, an "
        f"owner/repo, or a URL.")
