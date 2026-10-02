"""Tests for the SQLite store and the incremental re-run path.

The incrementality test is the point of the layer: a second run over an
unchanged source must fetch NOTHING. Proven with a fake source that counts
its own fetches — no network, no mock framework, just a counter.
"""

import os
import sys
import tempfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from faultline import cli  # noqa: E402
from faultline.model import Store  # noqa: E402
from faultline.sources import Commit, FetchResult, RepoRef  # noqa: E402


def test_store_roundtrip(tmp_path):
    with Store(tmp_path / "m.db") as st:
        st.record_fetch("a", "sha1", "main", 10, 1000, "2026-09-30")
        assert st.known_sha("a") == "sha1"
        assert st.is_current("a", "sha1")
        assert not st.is_current("a", "sha2")
        assert not st.is_current("never-fetched", "sha1")
        assert st.fetch_record("a") == (10, 1000)
        st.record_fetch("a", "sha2", "main", 11, 1100, "2026-10-01")
        assert st.known_sha("a") == "sha2"


def test_snapshots_accumulate_newest_first(tmp_path):
    with Store(tmp_path / "m.db") as st:
        st.snapshot("2026-09-29", repos=3, files_parsed=30, seams=1, certificates=2)
        st.snapshot("2026-09-30", repos=4, files_parsed=44, seams=2, certificates=1)
        h = st.history()
        assert [r[0] for r in h] == ["2026-09-30", "2026-09-29"]
        assert h[0][1:] == (4, 44, 2, 1)


def test_commits_are_deduplicated(tmp_path):
    with Store(tmp_path / "m.db") as st:
        cs = [Commit(sha="a" * 40, date="2026-09-30", message="x", author="y")]
        st.record_commits("r", cs)
        st.record_commits("r", cs)
        n = st.db.execute("SELECT COUNT(*) FROM commits").fetchone()[0]
        assert n == 1


def test_unchecked_is_recorded_per_snapshot(tmp_path):
    with Store(tmp_path / "m.db") as st:
        sid = st.snapshot("2026-09-30", 1, 1, 0, 0)
        st.record_unchecked(sid, "r", "certificate", "content check could not run")
        row = st.db.execute("SELECT repo, reason FROM unchecked").fetchone()
        assert row == ("r", "content check could not run")


class FakeSource:
    """A source whose HEAD moves only when the test says so."""
    name = "fake"
    authenticated = True

    def __init__(self, content_dir):
        self.dir = Path(content_dir)
        self.fetches = 0
        self.sha = "a" * 40

    def list_repos(self):
        return [RepoRef(id="fake/one", name="one", default_branch="main",
                        size_kb=10)]

    def head_sha(self, repo):
        return self.sha

    def fetch(self, repo, dest):
        self.fetches += 1
        root = Path(dest) / repo.name
        root.mkdir(parents=True, exist_ok=True)
        for f in self.dir.iterdir():
            (root / f.name).write_text(f.read_text())
        return FetchResult(repo=repo.id, ref="main", commit_sha=self.sha,
                           files=len(list(self.dir.iterdir())),
                           bytes=100, path=root, complete=True)


def test_unchanged_head_is_not_refetched(monkeypatch, tmp_path):
    src_dir = tmp_path / "origin"
    src_dir.mkdir()
    (src_dir / "pyproject.toml").write_text('[project]\nname="one"\n')
    (src_dir / "main.py").write_text("import json\n\nprint(json.dumps({}))\n")
    fake = FakeSource(src_dir)
    monkeypatch.setattr(cli, "open_source", lambda target: fake)

    workdir = tmp_path / "work"
    cli.analyze("fake/one", workdir, reuse=False)
    assert fake.fetches == 1

    cli.analyze("fake/one", workdir, reuse=False)
    assert fake.fetches == 1, "an unchanged HEAD was re-downloaded"

    fake.sha = "b" * 40                       # the repo moved
    cli.analyze("fake/one", workdir, reuse=False)
    assert fake.fetches == 2, "a moved HEAD was not re-fetched"
