"""Worker: claim loop, one child process per job in its own process group, cleanup timer.

`python -m app.worker` runs the loop; `python -m app.worker child <job_id>` is the per-job child.
"""

import importlib
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import time

from . import config, db, jobs
from .tools import NAMES, ToolError

log = logging.getLogger("worker")

CHILD = [sys.executable, "-m", "app.worker", "child"]  # tests swap in a scripted child


def recover() -> None:
    """On start: a job left processing by a crash or restart is failed; queued jobs stay queued."""
    with db.jobs() as c:
        c.execute(
            "UPDATE jobs SET status = 'failed', stage = NULL, message = 'Interrupted by a restart.', finished = ? "
            "WHERE status = 'processing'",
            (time.time(),),
        )


def cleanup() -> None:
    """Delete jobs finished more than RETENTION ago, and orphan dirs (no row) older than RETENTION."""
    cutoff = time.time() - config.RETENTION_SECONDS
    with db.jobs() as c:
        expired = c.execute(
            "DELETE FROM jobs WHERE status IN ('completed', 'failed', 'cancelled') AND finished < ? RETURNING id",
            (cutoff,),
        ).fetchall()
        live = {row[0] for row in c.execute("SELECT id FROM jobs")}
    for (job_id,) in expired:
        shutil.rmtree(config.JOBS_DIR / job_id, ignore_errors=True)
    for entry in os.scandir(config.JOBS_DIR):
        # The age check spares a dir whose upload is still streaming in (row not inserted yet).
        if (
            entry.is_dir(follow_symlinks=False)
            and entry.name not in live
            and entry.stat(follow_symlinks=False).st_mtime < cutoff
        ):
            shutil.rmtree(entry.path, ignore_errors=True)


_next_cleanup = 0.0  # shared by the claim loop and run_job, so files expire on time during a long job


def cleanup_if_due() -> None:
    global _next_cleanup
    if time.monotonic() >= _next_cleanup:
        cleanup()
        _next_cleanup = time.monotonic() + config.CLEANUP_INTERVAL_SECONDS


def _kill_group(pgid: int) -> None:
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def run_job(job_id: str) -> None:
    """Run one claimed job in a child process; kill its whole process group on cancel or timeout."""
    job_dir = config.JOBS_DIR / job_id
    # Own session = own process group, so killpg also takes down grandchildren such as Ghostscript.
    # TMPDIR on the jobs volume: temp files count against the disk budget, not the container's RAM.
    proc = subprocess.Popen([*CHILD, job_id], start_new_session=True, env={**os.environ, "TMPDIR": str(job_dir)})
    deadline = time.monotonic() + config.JOB_TIMEOUT_SECONDS
    timed_out = False
    while True:
        try:
            proc.wait(timeout=1)
            break
        except subprocess.TimeoutExpired:
            pass
        cleanup_if_due()
        with db.jobs() as c:
            row = jobs.get(c, job_id)
        if row is None or row["status"] == "cancelled":
            break
        if time.monotonic() > deadline:
            timed_out = True
            break
    _kill_group(proc.pid)  # the child if still running, plus anything it left behind
    proc.wait()

    with db.jobs() as c:
        if timed_out:
            row = jobs.get(c, job_id)
            if row and json.loads(row["outputs"]):
                jobs.finish(c, job_id, "completed", "Time limit reached: this is the best result so far.")
            else:
                jobs.finish(c, job_id, "failed", "Time limit reached before any result was produced. Try fewer or smaller files.")
        jobs.finish(c, job_id, "failed", "Processing failed.")  # no-op unless the child died without finishing
        row = jobs.get(c, job_id)
        if row is not None and row["status"] == "cancelled":
            c.execute("DELETE FROM jobs WHERE id = ?", (job_id,))  # deleted by its owner: the file names go too
    if row is None or row["status"] == "cancelled":
        shutil.rmtree(job_dir, ignore_errors=True)  # web only flips the status; files go once the child is dead


def child(job_id: str) -> None:
    """Body of the per-job child process: run the tool and record the result."""
    with db.jobs() as c:
        row = jobs.get(c, job_id)

    def report(stage=None, outputs=None):
        with db.jobs() as c:
            jobs.report(c, job_id, stage, outputs)

    try:
        if row["tool"] not in NAMES:
            raise ToolError("Unknown tool.")
        tool = importlib.import_module(f"app.tools.{row['tool']}")
        message = tool.run(config.JOBS_DIR / job_id, json.loads(row["inputs"]), json.loads(row["options"]), report)
        status = "completed"
    except ToolError as e:
        status, message = "failed", str(e)
    except Exception:
        log.exception("job %s failed", job_id)
        status, message = "failed", "Processing failed."
    with db.jobs() as c:
        jobs.finish(c, job_id, status, message)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if sys.argv[1:2] == ["child"]:
        child(sys.argv[2])
        return
    recover()
    while True:
        cleanup_if_due()
        with db.jobs() as c:
            job = jobs.claim(c)
        if job:
            log.info("job %s (%s) started", job["id"], job["tool"])
            run_job(job["id"])
            log.info("job %s done", job["id"])
        else:
            time.sleep(1)


if __name__ == "__main__":
    main()
