"""jobs.sqlite (web + worker), in WAL mode."""

import sqlite3
from contextlib import contextmanager

from . import config

JOBS_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,                -- UUID; files live in <JOBS_DIR>/<id>/in/<n> and out/<n>
    -- sha256 of the creating browser's cookie token; the token is never stored. The default lets a rollback to an
    -- older version, which does not know the column, keep inserting; no session ever matches ''.
    session TEXT NOT NULL DEFAULT '',
    tool TEXT NOT NULL,
    options TEXT NOT NULL,              -- JSON object
    status TEXT NOT NULL,               -- queued | processing | completed | failed | cancelled
    stage TEXT,
    created REAL NOT NULL,
    finished REAL,
    message TEXT,
    inputs TEXT NOT NULL,               -- JSON [{name, size}]
    outputs TEXT NOT NULL DEFAULT '[]'  -- JSON [{name, size, type}]: best result recorded so far
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs (status, created);
"""


def _has_session(conn) -> bool:
    return any(col["name"] == "session" for col in conn.execute("PRAGMA table_info(jobs)"))


@contextmanager
def jobs():
    # Autocommit: every statement we run is atomic on its own.
    conn = sqlite3.connect(config.JOBS_DIR / "jobs.sqlite", timeout=10, isolation_level=None)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(JOBS_SCHEMA)
        if not _has_session(conn):
            # A 1.1.0 table: its jobs belong to no browser, so drop them; cleanup() sweeps their folders as orphans.
            conn.execute("BEGIN IMMEDIATE")
            if not _has_session(conn):  # web and worker may both get here; the second finds it done
                conn.execute("DROP TABLE IF EXISTS jobs")
            conn.execute("COMMIT")
            conn.executescript(JOBS_SCHEMA)
        yield conn
    finally:
        conn.close()
