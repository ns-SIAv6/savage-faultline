"""The persistent store. SQLite; keyed by (repo, commit_sha).

The binding constraint of the whole tool is the request budget (60/hour
anonymous, 5,000 authenticated), so the store's first job is making re-runs
cheap: a repo whose HEAD has not moved is not re-downloaded. Its second job
is memory: every run is a snapshot, so "what changed since last time" is a
query, not a guess.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS repos (
    name TEXT PRIMARY KEY,
    full_name TEXT,
    owner TEXT,
    default_branch TEXT,
    language TEXT,
    description TEXT
);
CREATE TABLE IF NOT EXISTS fetches (
    repo TEXT NOT NULL,
    commit_sha TEXT,
    ref TEXT,
    files INTEGER,
    bytes INTEGER,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (repo, commit_sha)
);
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    taken_at TEXT NOT NULL,
    repos INTEGER,
    files_parsed INTEGER,
    seams INTEGER,
    certificates INTEGER,
    detail TEXT
);
CREATE TABLE IF NOT EXISTS commits (
    repo TEXT NOT NULL,
    sha TEXT NOT NULL,
    date TEXT,
    author TEXT,
    message TEXT,
    PRIMARY KEY (repo, sha)
);
CREATE TABLE IF NOT EXISTS unchecked (
    snapshot_id INTEGER,
    repo TEXT,
    check_name TEXT,
    reason TEXT
);
"""


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.db = sqlite3.connect(str(self.path))
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    # ------------------------------------------------------------- repos

    def upsert_repo(self, name, full_name, owner, branch, language, description):
        self.db.execute(
            "INSERT OR REPLACE INTO repos VALUES (?,?,?,?,?,?)",
            (name, full_name, owner, branch, language, description))

    # ------------------------------------------------------------ fetches

    def record_fetch(self, repo, commit_sha, ref, files, size_bytes, taken_at):
        self.db.execute(
            "INSERT OR REPLACE INTO fetches VALUES (?,?,?,?,?,?)",
            (repo, commit_sha, ref, files, size_bytes, taken_at))
        self.db.commit()

    def known_sha(self, repo):
        """The commit sha of the most recent recorded fetch, or None."""
        row = self.db.execute(
            "SELECT commit_sha FROM fetches WHERE repo=? "
            "ORDER BY fetched_at DESC LIMIT 1", (repo,)).fetchone()
        return row[0] if row else None

    def fetch_record(self, repo):
        """(files, bytes) of the most recent recorded fetch, for manifests."""
        row = self.db.execute(
            "SELECT files, bytes FROM fetches WHERE repo=? "
            "ORDER BY fetched_at DESC LIMIT 1", (repo,)).fetchone()
        return row or (0, 0)

    def is_current(self, repo, commit_sha):
        """True when the recorded fetch already covers this sha."""
        return (commit_sha is not None
                and self.known_sha(repo) == commit_sha)

    # ------------------------------------------------------------ history

    def record_commits(self, repo, commits):
        self.db.executemany(
            "INSERT OR IGNORE INTO commits VALUES (?,?,?,?,?)",
            [(repo, c.sha, c.date, c.author, c.message) for c in commits])
        self.db.commit()

    def snapshot(self, taken_at, repos, files_parsed, seams, certificates,
                 detail=None):
        cur = self.db.execute(
            "INSERT INTO snapshots (taken_at, repos, files_parsed, seams, "
            "certificates, detail) VALUES (?,?,?,?,?,?)",
            (taken_at, repos, files_parsed, seams, certificates,
             json.dumps(detail or {})))
        self.db.commit()
        return cur.lastrowid

    def record_unchecked(self, snapshot_id, repo, check_name, reason):
        self.db.execute(
            "INSERT INTO unchecked VALUES (?,?,?,?)",
            (snapshot_id, repo, check_name, reason))
        self.db.commit()

    def history(self, limit=10):
        return self.db.execute(
            "SELECT taken_at, repos, files_parsed, seams, certificates "
            "FROM snapshots ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
