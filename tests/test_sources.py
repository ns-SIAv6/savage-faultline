"""Source-adapter tests.

The network is not mocked and not needed: everything asserted here is a pure
function of input strings or of an in-memory tarball. The live path was
verified against real GitHub at integration time; these tests guard the parts
that can rot silently — target parsing, token resolution, tarball unpacking.
"""

import io
import os
import sys
import tarfile
import tempfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from faultline.sources import SourceError, open_source  # noqa: E402
from faultline.sources.github import GitHubSource, _owner_repo, resolve_token  # noqa: E402


def test_bare_owner_is_a_github_target():
    s = open_source("example-org")
    assert isinstance(s, GitHubSource)
    assert s.owner == "example-org" and s.repo is None


def test_owner_repo_and_url_forms():
    assert _owner_repo("a/b") == ("a", "b")
    assert _owner_repo("https://github.com/a/b") == ("a", "b")
    assert _owner_repo("https://github.com/a/b.git") == ("a", "b")
    assert _owner_repo("https://github.com/a/b/") == ("a", "b")
    assert _owner_repo("a") == ("a", None)


def test_existing_path_is_never_mistaken_for_a_remote():
    s = open_source(tempfile.gettempdir())
    assert s.name == "local"


def test_nonsense_target_is_rejected():
    try:
        open_source("/definitely/not/a/real/path/anywhere")
    except SourceError:
        pass
    else:
        raise AssertionError("a nonexistent absolute path must not resolve")


def test_token_resolution_order(monkeypatch):
    assert resolve_token("explicit") == "explicit"
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GH_TOKEN", "from-gh")
    assert resolve_token(None) == "from-gh"
    monkeypatch.setenv("GITHUB_TOKEN", "from-github")
    assert resolve_token(None) == "from-github"
    monkeypatch.delenv("GITHUB_TOKEN")
    monkeypatch.delenv("GH_TOKEN")
    assert resolve_token(None) is None


def _tar_bytes(files):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, content in files.items():
            data = content.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_fetch_unpacks_tarball_and_captures_commit(monkeypatch, tmp_path):
    """The leading owner-repo-<sha> component is stripped; junk dirs and binary
    extensions are skipped; the sha in the component becomes the commit id."""
    blob = _tar_bytes({
        "owner-repo-deadbeef1234/src/main.py": "print('hi')\n",
        "owner-repo-deadbeef1234/node_modules/x/index.js": "junk\n",
        "owner-repo-deadbeef1234/logo.png": "not-really-png",
        "owner-repo-deadbeef1234/README.md": "# hi\n",
    })
    src = GitHubSource("owner/repo", token="x")
    monkeypatch.setattr(src, "_request", lambda path, raw=False: blob)
    from faultline.sources import RepoRef
    ref = RepoRef(id="owner/repo", name="repo", default_branch="main")
    res = src.fetch(ref, tmp_path)
    assert res.files == 2 and res.skipped == 2
    assert res.commit_sha == "deadbeef1234"
    assert (tmp_path / "repo" / "src" / "main.py").read_text() == "print('hi')\n"
    assert not (tmp_path / "repo" / "node_modules").exists()
    assert not (tmp_path / "repo" / "logo.png").exists()


def test_anonymous_fetch_says_so(monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    blob = _tar_bytes({"o-r-abcd123/f.py": "x = 1\n"})
    src = GitHubSource("o/r")
    assert src.authenticated is False
    monkeypatch.setattr(src, "_request", lambda path, raw=False: blob)
    from faultline.sources import RepoRef
    res = src.fetch(RepoRef(id="o/r", name="r"), tmp_path)
    assert any("anonymously" in n for n in res.notes)
