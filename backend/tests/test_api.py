import io
import json
import os
import re
import threading
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor

from argon2.exceptions import VerificationError
from conftest import ORIGIN, PASSWORD, USERNAME, image_bytes, job_dirs, run_next_job, upload
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient
from PIL import Image

from app import api, auth, cli, config, db


def test_anonymous_gets_401(client):
    job = f"/api/jobs/{uuid.uuid4()}"
    assert client.get(job).status_code == 401
    assert client.get(job + "/files/0").status_code == 401
    assert client.delete(job).status_code == 401
    assert upload(client, [("a.bin", b"x")]).status_code == 401
    assert client.get("/api/session").json() == {"authenticated": False}
    assert job_dirs() == []


def test_login_sets_strict_cookie_and_logout_ends_session(logged_in):
    assert logged_in.get("/api/session").json() == {"authenticated": True}
    assert logged_in.post("/api/logout").status_code == 204
    assert logged_in.get("/api/session").json() == {"authenticated": False}


def test_cookie_flags(client):
    r = client.post("/api/login", json={"username": USERNAME, "password": PASSWORD})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "secure" in cookie and "samesite=strict" in cookie
    with db.state() as c:  # only the hash is stored
        (stored,) = c.execute("SELECT token_hash FROM sessions").fetchone()
    assert stored != client.cookies["session"]


def test_lan_host_gets_a_cookie_that_works_over_plain_http(client, monkeypatch):
    monkeypatch.setattr(config, "LAN_HOST", "192.168.0.102:8080")
    lan = "http://192.168.0.102:8080"
    plain = TestClient(api.app, base_url=lan, headers={"Origin": lan, "X-LFT": "1"})
    r = plain.post("/api/login", json={"username": USERNAME, "password": PASSWORD})
    cookie = r.headers["set-cookie"].lower()
    assert "secure" not in cookie and "httponly" in cookie and "samesite=strict" in cookie
    assert plain.get("/api/session").json() == {"authenticated": True}
    # Any other host, the tunnel's included, still gets the Secure cookie.
    r = client.post("/api/login", json={"username": USERNAME, "password": PASSWORD})
    assert "secure" in r.headers["set-cookie"].lower()


def guest_client():
    return TestClient(api.app, base_url=ORIGIN, headers={"Origin": ORIGIN, "X-LFT": "1"})


def test_guest_link_logs_in_until_revoked(capsys):
    cli.main(["invite", "ravi", "--hours", "1", "--url", "https://files.example.com/"])
    (link,) = [line for line in capsys.readouterr().out.splitlines() if "#invite=" in line]
    assert link.startswith("https://files.example.com/#invite=")
    token = link.split("#invite=")[1]
    with db.state() as c:  # only the hash is stored
        assert c.execute("SELECT token_hash FROM invites").fetchone()[0] != token

    guest = guest_client()
    assert guest.post("/api/invite", json={"token": "not-a-real-token"}).status_code == 401
    r = guest.post("/api/invite", json={"token": token})
    cookie = r.headers["set-cookie"].lower()
    assert r.status_code == 204 and "httponly" in cookie and "secure" in cookie and "samesite=strict" in cookie
    assert 3590 <= int(re.search(r"max-age=(\d+)", cookie).group(1)) <= 3600  # the cookie dies with the link
    assert upload(guest, [("a.png", image_bytes())]).status_code == 201

    # Logging out only clears this browser; the link still works, also on another device.
    assert guest.post("/api/logout").status_code == 204
    assert guest.get("/api/session").json() == {"authenticated": False}
    assert guest.post("/api/invite", json={"token": token}).status_code == 204

    cli.main(["invites"])
    assert "ravi: 1.0 hours left" in capsys.readouterr().out
    cli.main(["revoke", "ravi"])
    assert guest.get("/api/session").json() == {"authenticated": False}
    assert guest.post("/api/invite", json={"token": token}).status_code == 401


def test_expired_guest_link_is_rejected():
    token = auth.create_invite("old", 1)
    guest = guest_client()
    assert guest.post("/api/invite", json={"token": token}).status_code == 204
    with db.state() as c:
        c.execute("UPDATE invites SET expires = 0")
    assert guest.get("/api/session").json() == {"authenticated": False}
    assert guest.post("/api/invite", json={"token": token}).status_code == 401


def test_guest_link_needs_the_csrf_header():
    token = auth.create_invite("x", 1)
    r = TestClient(api.app, base_url=ORIGIN, headers={"Origin": "https://evil.example", "X-LFT": "1"}).post(
        "/api/invite", json={"token": token}
    )
    assert r.status_code == 403


def test_expired_session_is_rejected(logged_in):
    with db.state() as c:
        c.execute("UPDATE sessions SET expires = 0")
    assert logged_in.get(f"/api/jobs/{uuid.uuid4()}").status_code == 401
    assert logged_in.get("/api/session").json() == {"authenticated": False}


def test_cross_origin_post_rejected(logged_in):
    assert upload(logged_in, [("a.bin", b"x")], headers={"Origin": "https://evil.example"}).status_code == 403
    no_origin = {k: v for k, v in logged_in.headers.items() if k.lower() != "origin"}
    logged_in.headers = no_origin
    assert upload(logged_in, [("a.bin", b"x")]).status_code == 403


def test_missing_custom_header_rejected(logged_in):
    del logged_in.headers["X-LFT"]
    assert upload(logged_in, [("a.bin", b"x")]).status_code == 403
    assert logged_in.post("/api/logout").status_code == 403
    assert job_dirs() == []


def test_login_cross_origin_rejected(client):
    r = client.post("/api/login", json={"username": USERNAME, "password": PASSWORD}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_login_rate_limit(client):
    for _ in range(5):
        assert client.post("/api/login", json={"username": USERNAME, "password": "wrong"}).status_code == 401
    # Locked out now, even with the right password; another IP is not.
    assert client.post("/api/login", json={"username": USERNAME, "password": PASSWORD}).status_code == 429
    other = client.post("/api/login", json={"username": USERNAME, "password": PASSWORD}, headers={"CF-Connecting-IP": "203.0.113.9"})
    assert other.status_code == 204


def wrong_logins(peer, claimed_ips):
    """Status of one failed login per claimed address, all sent over a connection from `peer`."""
    c = TestClient(api.app, base_url=ORIGIN, headers={"Origin": ORIGIN, "X-LFT": "1"}, client=(peer, 50000))
    return [
        c.post("/api/login", json={"username": USERNAME, "password": "wrong"}, headers={"CF-Connecting-IP": ip}).status_code
        for ip in claimed_ips
    ]


def test_address_header_is_believed_only_from_the_proxy(client, monkeypatch):
    monkeypatch.setattr(config, "PROXY_IPS", "172.16.0.0/12")
    claimed = [f"203.0.113.{n}" for n in range(6)]
    # A LAN device inventing a new address per guess is still one client.
    assert wrong_logins("192.168.0.50", claimed) == [401] * 5 + [429]
    # Through the tunnel the header is real: six visitors, six separate counters.
    assert wrong_logins("172.19.0.1", claimed) == [401] * 6


def test_one_ipv6_network_is_one_client(client):
    assert wrong_logins("testclient", [f"2001:db8:1:2::{n:x}" for n in range(1, 7)]) == [401] * 5 + [429]
    assert wrong_logins("testclient", ["2001:db8:1:3::1"]) == [401]


def test_password_checks_run_one_at_a_time(client, monkeypatch):
    """Each real check takes 64 MiB; in parallel they could exhaust the container's memory."""
    count_lock, state = threading.Lock(), {"running": 0, "peak": 0}

    class SlowHasher:
        def verify(self, hash, password):
            with count_lock:
                state["running"] += 1
                state["peak"] = max(state["peak"], state["running"])
            time.sleep(0.02)
            with count_lock:
                state["running"] -= 1
            raise VerificationError

    def attempt(n):
        try:
            auth.login(f"203.0.113.{n}", USERNAME, "wrong")
        except Exception:
            pass

    monkeypatch.setattr(auth, "_hasher", SlowHasher())
    with ThreadPoolExecutor(8) as pool:
        list(pool.map(attempt, range(8)))
    assert state["peak"] == 1


def test_oversize_upload_rejected_by_content_length(logged_in, monkeypatch):
    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 1000)
    assert upload(logged_in, [("a.bin", b"x" * 2000)]).status_code == 413
    assert job_dirs() == []


def test_oversize_upload_rejected_while_streaming(logged_in, monkeypatch):
    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 1000)
    body = (
        b'--B\r\nContent-Disposition: form-data; name="tool"\r\n\r\nimage\r\n'
        b'--B\r\nContent-Disposition: form-data; name="files"; filename="a.bin"\r\n\r\n'
        + b"x" * 5000 + b"\r\n--B--\r\n"
    )
    # A generator body is sent chunked, without Content-Length, so only the streaming count can catch it.
    chunks = (body[i : i + 512] for i in range(0, len(body), 512))
    r = logged_in.post("/api/jobs", content=chunks, headers={"Content-Type": "multipart/form-data; boundary=B"})
    assert r.status_code == 413
    assert job_dirs() == []


def test_too_many_files_rejected(logged_in, monkeypatch):
    monkeypatch.setattr(config, "MAX_FILES", 2)
    assert upload(logged_in, [(f"{i}.bin", b"x") for i in range(3)]).status_code == 400
    assert job_dirs() == []
    assert upload(logged_in, [(f"{i}.bin", b"x") for i in range(2)]).status_code == 201


def test_queue_full_rejected(logged_in, monkeypatch):
    monkeypatch.setattr(config, "MAX_QUEUED", 1)
    assert upload(logged_in, [("a.bin", b"x")]).status_code == 201
    assert upload(logged_in, [("b.bin", b"x")]).status_code == 503
    assert len(job_dirs()) == 1


def test_disk_budget_exceeded_rejected(logged_in, monkeypatch):
    monkeypatch.setattr(config, "DISK_BUDGET_BYTES", 100_000)
    (config.JOBS_DIR / "leftover.bin").write_bytes(b"x" * 100_001)
    assert upload(logged_in, [("a.bin", b"x")]).status_code == 507
    assert job_dirs() == []


def test_bad_tool_and_options_rejected(logged_in):
    assert upload(logged_in, [("a.bin", b"x")], tool="nope").status_code == 400
    assert upload(logged_in, [("a.bin", b"x")], options="[1]").status_code == 400
    assert upload(logged_in, [("a.bin", b"x")], options="{bad").status_code == 400
    assert job_dirs() == []


def test_image_job_end_to_end(logged_in):
    png = image_bytes((300, 200))
    r = upload(logged_in, [("photo ünï.png", png), ("../../etc/passwd", png)], options='{"format": "webp"}')
    assert r.status_code == 201
    job_id = r.json()["id"]
    assert logged_in.get(f"/api/jobs/{job_id}").json()["status"] == "queued"
    assert sorted(os.listdir(config.JOBS_DIR / job_id / "in")) == ["0", "1"]  # names never reach the disk

    run_next_job()
    status = logged_in.get(f"/api/jobs/{job_id}").json()
    assert status["status"] == "completed", status
    assert [o["name"] for o in status["outputs"]] == ["photo ünï.webp", "passwd.webp"]

    r = logged_in.get(f"/api/jobs/{job_id}/files/0")
    assert Image.open(io.BytesIO(r.content)).format == "WEBP"
    assert r.headers["content-type"] == "image/webp"
    assert r.headers["content-disposition"].startswith("attachment;")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "no-store"
    assert "default-src 'self'" in r.headers["content-security-policy"]

    z = zipfile.ZipFile(io.BytesIO(logged_in.get(f"/api/jobs/{job_id}/zip").content))
    assert z.read("photo ünï.webp") == r.content and z.namelist() == ["photo ünï.webp", "passwd.webp"]

    assert logged_in.delete(f"/api/jobs/{job_id}").status_code == 204
    assert logged_in.get(f"/api/jobs/{job_id}").status_code == 404
    assert job_dirs() == []


def test_bad_image_fails_job_with_message(logged_in):
    job_id = upload(logged_in, [("notes.jpg", b"not an image")], options='{"format": "png"}').json()["id"]
    run_next_job()
    status = logged_in.get(f"/api/jobs/{job_id}").json()
    assert status["status"] == "failed" and "notes.jpg is not a supported image" in status["message"]


def test_hostile_pdf_name_cannot_inject_a_header(logged_in):
    options = json.dumps({"page": "image", "name": 'résumé "x"\r\nSet-Cookie: a=b/../../y'})
    job_id = upload(logged_in, [("a.png", image_bytes())], tool="images_to_pdf", options=options).json()["id"]
    run_next_job()
    assert logged_in.get(f"/api/jobs/{job_id}").json()["outputs"][0]["name"] == "résumé xSet-Cookie a=b....y.pdf"
    r = logged_in.get(f"/api/jobs/{job_id}/files/0")
    assert r.status_code == 200 and "set-cookie" not in r.headers
    assert r.headers["content-disposition"] == (
        "attachment; filename=\"r_sum_ xSet-Cookie a_b....y.pdf\"; "
        "filename*=UTF-8''r%C3%A9sum%C3%A9%20xSet-Cookie%20a%3Db....y.pdf"
    )


def _completed_job(logged_in):
    job_id = upload(logged_in, [("a.png", image_bytes())], options='{"format": "png"}').json()["id"]
    run_next_job()
    assert logged_in.get(f"/api/jobs/{job_id}/files/0").status_code == 200
    return job_id


def test_symlinked_output_file_not_served(logged_in):
    job_id = _completed_job(logged_in)
    secret = config.STATE_DIR / "state.sqlite"
    out = config.JOBS_DIR / job_id / "out" / "0"
    out.unlink()
    out.symlink_to(secret)
    assert logged_in.get(f"/api/jobs/{job_id}/files/0").status_code == 404
    assert logged_in.get(f"/api/jobs/{job_id}/zip").status_code == 404


def test_symlinked_output_dir_not_served(logged_in, tmp_path):
    job_id = _completed_job(logged_in)
    (tmp_path / "evil").mkdir()
    (tmp_path / "evil" / "0").write_bytes(b"secret")
    out = config.JOBS_DIR / job_id / "out"
    (out / "0").unlink()
    out.rmdir()
    out.symlink_to(tmp_path / "evil")
    assert logged_in.get(f"/api/jobs/{job_id}/files/0").status_code == 404


def test_fifo_output_not_served(logged_in):
    job_id = _completed_job(logged_in)
    out = config.JOBS_DIR / job_id / "out" / "0"
    out.unlink()
    os.mkfifo(out)
    assert logged_in.get(f"/api/jobs/{job_id}/files/0").status_code == 404


def test_untrusted_job_row_is_sanitised(logged_in):
    job_id = _completed_job(logged_in)
    with db.jobs() as c:
        c.execute(
            "UPDATE jobs SET outputs = ? WHERE id = ?",
            ('[{"name": "x\\r\\nSet-Cookie: a=b", "size": 6, "type": "text/html"}]', job_id),
        )
    r = logged_in.get(f"/api/jobs/{job_id}/files/0")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/octet-stream"
    assert "set-cookie" not in r.headers
    for garbage in ("{}", '[{"size": 1e999}]', "5"):
        with db.jobs() as c:
            c.execute("UPDATE jobs SET outputs = ? WHERE id = ?", (garbage, job_id))
        assert logged_in.get(f"/api/jobs/{job_id}").json()["outputs"] == []


def test_oversize_login_body_rejected(client):
    r = client.post("/api/login", json={"username": USERNAME, "password": "x" * 100_000})
    assert r.status_code == 413


def test_cache_headers(client, tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "index-abc123.js").write_text("export {}")
    (tmp_path / "index.html").write_text("<!doctype html>")
    (tmp_path / "manifest.webmanifest").write_text("{}")
    site = FastAPI()
    site.mount("/", StaticFiles(directory=tmp_path, html=True))
    static = TestClient(api.security_headers(site))
    assert static.get("/assets/index-abc123.js").headers["cache-control"] == "public, max-age=31536000, immutable"
    assert static.get("/assets/gone.js").headers["cache-control"] == "no-cache"  # a 404 must not stick for a year
    assert static.get("/").headers["cache-control"] == "no-cache"
    assert static.get("/manifest.webmanifest").headers["cache-control"] == "no-cache"
    assert client.get("/api/session").headers["cache-control"] == "no-store"
