"""Read repositories from GitHub.

Credential-free operation is a first-class mode: public repositories work with
no token at all. The binding constraint is the request budget — 60/hour
anonymous against 5,000/hour authenticated — so fetching is always ONE request
per repository (a tarball), never one per file. At one request per file a
no-credential user gets through roughly one large repo; at one per repo they
get through sixty an hour.

When the budget is exhausted this raises `RateLimited` with the remedy in the
message, rather than appearing to hang.
"""

from __future__ import annotations

import io
import json
import os
import re
import tarfile
import urllib.error
import urllib.request
from pathlib import Path

from . import Commit, FetchResult, RateLimited, RepoRef, SourceError, register

API = "https://api.github.com"

# What cannot contribute import edges or coupling identifiers.
SKIP_DIRS = ("node_modules/", "__pycache__/", ".git/", "dist/", "build/",
             ".venv/", "venv/", ".next/", "vendor/", "Pods/", ".gradle/",
             "target/", "coverage/", ".idea/", "site-packages/")

BINARY_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".svg", ".pdf",
              ".zip", ".tar", ".gz", ".whl", ".jar", ".aar", ".apk", ".so",
              ".dylib", ".dll", ".exe", ".bin", ".woff", ".woff2", ".ttf",
              ".otf", ".mp3", ".mp4", ".mov", ".wav", ".psd", ".sketch",
              ".lock", ".keystore", ".jks", ".pb", ".onnx", ".pt", ".pkl",
              ".h5", ".wasm", ".class", ".pyc"}


def resolve_token(token: str | None = None) -> str | None:
    """Explicit argument wins, then the environment. None is a valid answer."""
    if token:
        return token
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or None


def _owner_repo(target: str) -> tuple[str, str | None]:
    """'https://github.com/a/b.git' | 'a/b' | 'a' -> ('a', 'b' | None)."""
    t = target.strip().rstrip("/")
    t = re.sub(r"^https?://(www\.)?github\.com/", "", t, flags=re.I)
    t = re.sub(r"\.git$", "", t)
    parts = [p for p in t.split("/") if p]
    if not parts:
        raise SourceError(f"Cannot read a GitHub owner from {target!r}")
    return parts[0], parts[1] if len(parts) > 1 else None


@register("github")
class GitHubSource:
    name = "github"

    def __init__(self, target: str, token: str | None = None, **_):
        self.owner, self.repo = _owner_repo(target)
        self.token = resolve_token(token)
        self.authenticated = self.token is not None

    # ------------------------------------------------------------ http

    def _request(self, path: str, raw: bool = False):
        url = path if path.startswith("http") else f"{API}{path}"
        req = urllib.request.Request(url, headers={"User-Agent": "faultline"})
        req.add_header("Accept", "application/vnd.github+json")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                body = r.read()
                return body if raw else json.loads(body.decode())
        except urllib.error.HTTPError as e:
            if e.code in (403, 429):
                remaining = e.headers.get("X-RateLimit-Remaining")
                retry = e.headers.get("Retry-After")
                if remaining == "0" or e.code == 429:
                    remedy = (
                        "GitHub refused more requests. Unauthenticated access "
                        "allows 60 requests/hour; a token allows 5,000. Add "
                        "GITHUB_TOKEN to the environment and retry."
                        if not self.authenticated else
                        "GitHub rate limit reached for this token (5,000/hour). "
                        "Wait for the window to reset and retry.")
                    raise RateLimited(remedy,
                                      int(retry) if retry and retry.isdigit() else None)
            if e.code == 404:
                raise SourceError(
                    f"GitHub says {url} does not exist. If the repository is "
                    f"private, a token with access is required (GITHUB_TOKEN).")
            raise SourceError(f"GitHub HTTP {e.code} for {url}: "
                              f"{e.read()[:200].decode(errors='replace')}")
        except urllib.error.URLError as e:
            raise SourceError(f"Cannot reach GitHub: {e.reason}")

    # ------------------------------------------------------------ repos

    def list_repos(self) -> list[RepoRef]:
        if self.repo:
            r = self._request(f"/repos/{self.owner}/{self.repo}")
            return [self._ref(r)]
        # An owner can be an org or a user; the API answers one or the other.
        try:
            batch = self._paged(f"/orgs/{self.owner}/repos")
        except SourceError:
            batch = self._paged(f"/users/{self.owner}/repos")
        return [self._ref(r) for r in batch]

    def _paged(self, path: str) -> list[dict]:
        out, page = [], 1
        while True:
            sep = "&" if "?" in path else "?"
            batch = self._request(f"{path}{sep}per_page=100&page={page}")
            if not batch:
                return out
            out.extend(batch)
            if len(batch) < 100:
                return out
            page += 1

    @staticmethod
    def _ref(r: dict) -> RepoRef:
        return RepoRef(
            id=r["full_name"],
            name=r["name"],
            default_branch=r.get("default_branch", "main"),
            size_kb=r.get("size", 0),
            description=r.get("description"),
            language=r.get("language"),
            archived=bool(r.get("archived")),
        )

    # ------------------------------------------------------------ fetching

    def fetch(self, repo: RepoRef, dest: Path) -> FetchResult:
        """One request: the whole repository as a tarball, unpacked under dest."""
        blob = self._request(f"/repos/{repo.id}/tarball/{repo.default_branch}",
                             raw=True)
        root = Path(dest) / repo.name
        files, skipped, total = 0, 0, len(blob)
        sha = None
        try:
            with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tf:
                for m in tf.getmembers():
                    if not m.isfile():
                        continue
                    parts = m.name.split("/", 1)
                    if len(parts) < 2:      # the leading owner-repo-sha/ component
                        continue
                    if sha is None:
                        # the tarball root is named owner-repo-<short sha>; it is
                        # the cheapest commit identity available — no extra request
                        sha = parts[0].rsplit("-", 1)[-1] or None
                    rel = parts[1]
                    if any(s in rel for s in SKIP_DIRS):
                        skipped += 1
                        continue
                    if os.path.splitext(rel)[1].lower() in BINARY_EXT:
                        skipped += 1
                        continue
                    target = root / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    src = tf.extractfile(m)
                    if src is None:
                        skipped += 1
                        continue
                    target.write_bytes(src.read())
                    files += 1
        except tarfile.TarError as e:
            raise SourceError(f"{repo.id}: GitHub returned a bad tarball ({e})")
        notes = ([] if self.authenticated
                 else ["fetched anonymously — 60 requests/hour budget"])
        return FetchResult(
            repo=repo.id, ref=repo.default_branch, commit_sha=sha,
            files=files, bytes=total, skipped=skipped, path=root,
            complete=True, notes=notes)

    def head_sha(self, repo: RepoRef) -> str | None:
        """The current HEAD sha — one small request, used to skip re-fetching
        a repo that has not moved."""
        batch = self._request(
            f"/repos/{repo.id}/commits?per_page=1&sha={repo.default_branch}")
        return batch[0]["sha"] if batch else None

    # ------------------------------------------------------------ history

    def commits(self, repo: RepoRef, limit: int = 100) -> list[Commit]:
        batch = self._request(
            f"/repos/{repo.id}/commits?per_page={min(limit, 100)}"
            f"&sha={repo.default_branch}")
        out = []
        for c in batch[:limit]:
            meta = c.get("commit", {})
            out.append(Commit(
                sha=c.get("sha", ""),
                date=(meta.get("author") or {}).get("date", ""),
                author=(meta.get("author") or {}).get("name"),
                message=(meta.get("message") or "").splitlines()[0] if meta.get("message") else "",
            ))
        return out
