"""Job rows in jobs.sqlite: create / claim / report / finish / delete, plus the jobs volume size."""

import json
import os
import time

from . import config


def queued_count(c) -> int:
    return c.execute("SELECT count(*) FROM jobs WHERE status = 'queued'").fetchone()[0]


def create(c, job_id: str, session: str, tool: str, options: dict, inputs: list[dict]) -> bool:
    """Insert a queued job unless the queue is already full (checked in the same statement). False when full."""
    cur = c.execute(
        "INSERT INTO jobs (id, session, tool, options, status, created, inputs) "
        "SELECT ?, ?, ?, ?, 'queued', ?, ? WHERE (SELECT count(*) FROM jobs WHERE status = 'queued') < ?",
        (job_id, session, tool, json.dumps(options), time.time(), json.dumps(inputs), config.MAX_QUEUED),
    )
    return cur.rowcount == 1


def get(c, job_id: str):
    return c.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()


def get_own(c, job_id: str, session: str):
    """The job if this session created it, else None, the same as for an unknown id."""
    return c.execute("SELECT * FROM jobs WHERE id = ? AND session = ?", (job_id, session)).fetchone()


def ids_of(c, session: str) -> list[str]:
    return [row[0] for row in c.execute("SELECT id FROM jobs WHERE session = ?", (session,))]


def position(c, row) -> int | None:
    """1-based place of a queued job in the order claim() takes them (1 = next); None unless queued."""
    if row["status"] != "queued":
        return None
    return c.execute(
        "SELECT count(*) FROM jobs WHERE status = 'queued' AND (created, id) <= (?, ?)", (row["created"], row["id"])
    ).fetchone()[0]


def claim(c):
    """Atomically move the oldest queued job to processing and return it, or None."""
    rows = c.execute(
        "UPDATE jobs SET status = 'processing', stage = 'Starting' "
        "WHERE id = (SELECT id FROM jobs WHERE status = 'queued' ORDER BY created, id LIMIT 1) AND status = 'queued' "
        "RETURNING *"
    ).fetchall()
    return rows[0] if rows else None


def report(c, job_id: str, stage: str | None = None, outputs: list[dict] | None = None) -> None:
    """Called from the job's child process as it goes; `outputs` is the best result so far."""
    if stage is not None:
        c.execute("UPDATE jobs SET stage = ? WHERE id = ?", (stage, job_id))
    if outputs is not None:
        c.execute("UPDATE jobs SET outputs = ? WHERE id = ?", (json.dumps(outputs), job_id))


def finish(c, job_id: str, status: str, message: str | None) -> None:
    """Set a final status; no-op unless the job is still processing (it may have been cancelled meanwhile)."""
    c.execute(
        "UPDATE jobs SET status = ?, message = ?, stage = NULL, finished = ? WHERE id = ? AND status = 'processing'",
        (status, message, time.time(), job_id),
    )


def delete(c, job_id: str, session: str) -> str | None:
    """Web side of DELETE: drop this session's job if it isn't running (caller deletes its files), or flag a running
    one as cancelled (the worker kills it, then deletes its files). Returns 'deleted', 'cancelled' or None."""
    for _ in range(2):  # retry once in case the job finished between the two statements
        if c.execute(
            "DELETE FROM jobs WHERE id = ? AND session = ? AND status != 'processing'", (job_id, session)
        ).rowcount:
            return "deleted"
        if c.execute(
            "UPDATE jobs SET status = 'cancelled', finished = ? WHERE id = ? AND session = ? AND status = 'processing'",
            (time.time(), job_id, session),
        ).rowcount:
            return "cancelled"
    return None


def disk_usage() -> int:
    """Bytes in the jobs volume (files only, symlinks not followed)."""
    total = 0
    for root, _, files in os.walk(config.JOBS_DIR):
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except FileNotFoundError:
                pass
    return total
