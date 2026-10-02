"""Where what-was-fetched lives between runs.

SQLite, stdlib, no server. The store answers two questions today: what did we
fetch for this repo, keyed by commit sha (so re-runs skip repos whose HEAD
did not move), and what did each run see (so history and the time dimension
have somewhere to land).
"""

from .store import Store

__all__ = ["Store"]
