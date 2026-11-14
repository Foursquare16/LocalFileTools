"""The two SQLite files: state.sqlite (web only) and jobs.sqlite (web + worker), both in WAL mode."""

import sqlite3
from contextlib import contextmanager

from . import config

STATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS owner (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    username TEXT NOT NULL,
    password_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,  -- sha256 of the cookie token; the token itself is never stored
    expires REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS invites (
    token_hash TEXT PRIMARY KEY,  -- sha256 of a guest link's token, which doubles as that guest's cookie
    name TEXT NOT NULL,
    expires REAL NOT NULL
);
"""

JOBS_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,                -- UUID; files live in <JOBS_DIR>/<id>/in/<n> and out/<n>
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


@contextmanager
def _connect(path, schema):
    # Autocommit: every statement we run is atomic on its own.
    conn = sqlite3.connect(path, timeout=10, isolation_level=None)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(schema)
        yield conn
    finally:
        conn.close()


def state():
    return _connect(config.STATE_DIR / "state.sqlite", STATE_SCHEMA)


def jobs():
    return _connect(config.JOBS_DIR / "jobs.sqlite", JOBS_SCHEMA)
