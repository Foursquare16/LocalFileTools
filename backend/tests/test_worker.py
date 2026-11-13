import os
import sys
import threading
import time
import uuid

import pytest
from conftest import is_alive, job_dirs, run_next_job, upload

from app import config, db, jobs, worker

# Stand-in for the per-job child: starts a grandchild in its process group (like Ghostscript), optionally
# records a result, then hangs. Written at runtime so the repo holds no test-only tool.
SLOW_CHILD = """
import json, os, subprocess, sys, time
from app import config, db, jobs

job_id = sys.argv[1]
job_dir = config.JOBS_DIR / job_id
sleeper = subprocess.Popen(["sleep", "60"])
with open(os.environ["TEST_PID_FILE"], "w") as f:
    f.write(str(sleeper.pid))
with db.jobs() as c:
    if json.loads(jobs.get(c, job_id)["options"]).get("produce"):
        (job_dir / "out").mkdir()
        (job_dir / "out" / "0").write_bytes(b"best so far")
        jobs.report(c, job_id, outputs=[{"name": "best.bin", "size": 11, "type": "application/octet-stream"}])
time.sleep(60)
"""


@pytest.fixture
def slow_child(tmp_path, monkeypatch):
    script = tmp_path / "slow_child.py"
    script.write_text(SLOW_CHILD)
    pid_file = tmp_path / "grandchild.pid"
    monkeypatch.setattr(worker, "CHILD", [sys.executable, str(script)])
    monkeypatch.setenv("TEST_PID_FILE", str(pid_file))
    return pid_file


def _wait_dead(pid, seconds=3):
    end = time.monotonic() + seconds
    while is_alive(pid) and time.monotonic() < end:
        time.sleep(0.05)
    return not is_alive(pid)


def _status(job_id):
    with db.jobs() as c:
        return jobs.get(c, job_id)


def test_child_gets_tmpdir_in_job_dir(logged_in, tmp_path, monkeypatch):
    script = tmp_path / "env_child.py"
    script.write_text("import os, sys; open(os.environ['TMPDIR'] + '/tmpdir.txt', 'w').write(os.environ['TMPDIR'])")
    monkeypatch.setattr(worker, "CHILD", [sys.executable, str(script)])
    job_id = upload(logged_in, [("a.bin", b"x")]).json()["id"]
    run_next_job()
    assert (config.JOBS_DIR / job_id / "tmpdir.txt").read_text() == str(config.JOBS_DIR / job_id)
    assert _status(job_id)["status"] == "failed"  # child exited without finishing the job


def test_cancel_kills_group_and_deletes_files(logged_in, slow_child):
    job_id = upload(logged_in, [("a.bin", b"x")]).json()["id"]
    threading.Timer(1.5, lambda: logged_in.delete(f"/api/jobs/{job_id}")).start()
    start = time.monotonic()
    run_next_job()
    assert time.monotonic() - start < 5
    assert _status(job_id)["status"] == "cancelled"
    assert job_dirs() == []  # the worker deleted the files once the child was dead
    assert _wait_dead(int(slow_child.read_text()))


def test_timeout_kills_whole_group_and_returns_best_so_far(logged_in, slow_child, monkeypatch):
    monkeypatch.setattr(config, "JOB_TIMEOUT_SECONDS", 2)
    job_id = upload(logged_in, [("a.bin", b"x")], options='{"produce": true}').json()["id"]
    start = time.monotonic()
    run_next_job()
    assert time.monotonic() - start < 6
    assert _wait_dead(int(slow_child.read_text()))  # the grandchild died with the child
    status = logged_in.get(f"/api/jobs/{job_id}").json()
    assert status["status"] == "completed"
    assert "Time limit" in status["message"]
    assert logged_in.get(f"/api/jobs/{job_id}/files/0").content == b"best so far"


def test_timeout_without_result_fails(logged_in, slow_child, monkeypatch):
    monkeypatch.setattr(config, "JOB_TIMEOUT_SECONDS", 2)
    job_id = upload(logged_in, [("a.bin", b"x")]).json()["id"]
    run_next_job()
    assert _wait_dead(int(slow_child.read_text()))
    row = _status(job_id)
    assert row["status"] == "failed" and "Time limit" in row["message"]


def test_restart_recovery():
    with db.jobs() as c:
        jobs.create(c, "running", "image", {}, [])
        jobs.create(c, "waiting", "image", {}, [])
        c.execute("UPDATE jobs SET created = created - 10 WHERE id = 'running'")
        assert jobs.claim(c)["id"] == "running"
    worker.recover()
    assert _status("running")["status"] == "failed"
    assert _status("waiting")["status"] == "queued"


def test_cleanup_after_expiry():
    old = time.time() - config.RETENTION_SECONDS - 10
    ids = {name: str(uuid.uuid4()) for name in ("expired", "recent", "queued", "orphan", "uploading")}
    for job_id in ids.values():
        (config.JOBS_DIR / job_id / "in").mkdir(parents=True)
    with db.jobs() as c:
        for name in ("expired", "recent", "queued"):
            jobs.create(c, ids[name], "image", {}, [])
        c.execute("UPDATE jobs SET status = 'completed', finished = ? WHERE id = ?", (old, ids["expired"]))
        c.execute("UPDATE jobs SET status = 'failed', finished = ? WHERE id = ?", (time.time(), ids["recent"]))
    os.utime(config.JOBS_DIR / ids["orphan"], (old, old))  # "uploading" is a fresh dir with no row yet

    worker.cleanup()

    assert set(job_dirs()) == {ids["recent"], ids["queued"], ids["uploading"]}
    assert _status(ids["expired"]) is None
    assert _status(ids["recent"]) is not None
