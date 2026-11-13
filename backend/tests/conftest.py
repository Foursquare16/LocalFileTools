import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import api, auth, config, db, jobs, worker

BACKEND = Path(__file__).resolve().parent.parent
USERNAME, PASSWORD = "owner", "correct horse battery"
ORIGIN = "https://testserver"


@pytest.fixture(autouse=True)
def dirs(tmp_path, monkeypatch):
    """Fresh state and jobs volumes per test; env too, for the worker's child processes."""
    for name in ("state", "jobs"):
        (tmp_path / name).mkdir()
    monkeypatch.setattr(config, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(config, "JOBS_DIR", tmp_path / "jobs")
    monkeypatch.setenv("LFT_JOBS_DIR", str(tmp_path / "jobs"))
    monkeypatch.setenv("PYTHONPATH", str(BACKEND))
    auth._failures.clear()


@pytest.fixture
def client():
    with db.state() as c:
        c.execute("INSERT INTO owner VALUES (1, ?, ?)", (USERNAME, auth.hash_password(PASSWORD)))
    # https so the Secure cookie is sent back; Origin + X-LFT as the frontend sends them.
    return TestClient(api.app, base_url=ORIGIN, headers={"Origin": ORIGIN, "X-LFT": "1"})


@pytest.fixture
def logged_in(client):
    r = client.post("/api/login", json={"username": USERNAME, "password": PASSWORD})
    assert r.status_code == 204, r.text
    return client


def upload(client, files, tool="image", options="{}", **kwargs):
    return client.post(
        "/api/jobs",
        data={"tool": tool, "options": options},
        files=[("files", (name, data)) for name, data in files],
        **kwargs,
    )


def image_bytes(size=(40, 30), fmt="PNG", mode="RGB", color="red", **save) -> bytes:
    buf = io.BytesIO()
    Image.new(mode, size, color).save(buf, fmt, **save)
    return buf.getvalue()


def run_next_job():
    with db.jobs() as c:
        job = jobs.claim(c)
    assert job is not None
    worker.run_job(job["id"])
    return job["id"]


def job_dirs():
    return sorted(p.name for p in config.JOBS_DIR.iterdir() if p.is_dir())


def is_alive(pid: int) -> bool:
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except FileNotFoundError:
        return False
    return state != "Z"
