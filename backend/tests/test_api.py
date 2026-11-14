import hashlib
import io
import json
import os
import re
import uuid
import zipfile

from conftest import image_bytes, job_dirs, new_client, run_next_job, upload
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from app import api, cli, config, db, jobs


def test_session_cookie_is_set_once_and_only_its_hash_is_stored(client):
    r = client.get("/api/session")
    assert r.status_code == 200 and r.json() == {"open": True}
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "secure" in cookie and "samesite=strict" in cookie and "max-age=604800" in cookie
    token = client.cookies["session"]
    assert len(token) == 43
    assert "set-cookie" not in client.get("/api/session").headers  # kept, not replaced
    upload(client, [("a.bin", b"x")])
    with db.jobs() as c:
        (stored,) = c.execute("SELECT session FROM jobs").fetchone()
    assert stored == hashlib.sha256(token.encode()).hexdigest()


def test_missing_or_malformed_cookie_gets_a_fresh_one_also_on_errors():
    for headers in ({}, {"Cookie": "session=not-a-token"}):
        r = new_client().get(f"/api/jobs/{uuid.uuid4()}", headers=headers)
        assert r.status_code == 404 and re.match(r"session=[\w-]{43};", r.headers["set-cookie"])


def test_lan_host_gets_a_cookie_that_works_over_plain_http(client, monkeypatch):
    monkeypatch.setattr(config, "LAN_HOST", "192.168.0.102:8080")
    lan = "http://192.168.0.102:8080"
    plain = TestClient(api.app, base_url=lan, headers={"Origin": lan, "X-LFT": "1"})
    cookie = plain.get("/api/session").headers["set-cookie"].lower()
    assert "secure" not in cookie and "httponly" in cookie and "samesite=strict" in cookie
    job_id = upload(plain, [("a.bin", b"x")]).json()["id"]
    assert plain.get(f"/api/jobs/{job_id}").status_code == 200  # the cookie came back
    # Any other host, the tunnel's included, still gets the Secure cookie.
    assert "secure" in client.get("/api/session").headers["set-cookie"].lower()


def test_another_session_cannot_see_or_touch_a_job(client):
    job_id = _completed_job(client)
    other = new_client()
    unknown = other.get(f"/api/jobs/{uuid.uuid4()}")
    assert unknown.status_code == 404
    for r in (
        other.get(f"/api/jobs/{job_id}"),
        other.get(f"/api/jobs/{job_id}/files/0"),
        other.get(f"/api/jobs/{job_id}/zip"),
        other.delete(f"/api/jobs/{job_id}"),
    ):
        assert (r.status_code, r.json()) == (404, unknown.json())
    assert other.delete("/api/jobs").status_code == 204
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "completed"
    assert job_dirs() == [job_id]


def test_delete_my_jobs_removes_rows_and_folders(client):
    mine = [upload(client, [("a.png", image_bytes())], options='{"format": "png"}').json()["id"] for _ in range(3)]
    run_next_job()  # one completed, two still queued
    theirs = upload(new_client(), [("b.bin", b"x")]).json()["id"]
    assert client.delete("/api/jobs").status_code == 204
    with db.jobs() as c:
        assert [row[0] for row in c.execute("SELECT id FROM jobs")] == [theirs]
    assert job_dirs() == [theirs]
    for job_id in mine:
        assert client.get(f"/api/jobs/{job_id}").status_code == 404
    assert client.delete("/api/jobs").status_code == 204  # nothing left is fine


def test_queue_position_counts_every_session_in_claim_order(client):
    other = new_client()
    first = upload(client, [("a.bin", b"x")]).json()["id"]
    second = upload(other, [("b.bin", b"x")]).json()["id"]
    third = upload(client, [("c.bin", b"x")]).json()["id"]
    places = {
        job_id: browser.get(f"/api/jobs/{job_id}").json()["position"]
        for job_id, browser in ((first, client), (second, other), (third, client))
    }
    assert sorted(places.values()) == [1, 2, 3]
    with db.jobs() as c:
        claimed = jobs.claim(c)["id"]
    assert places[claimed] == 1
    owner = {first: client, second: other, third: client}
    assert owner[claimed].get(f"/api/jobs/{claimed}").json()["position"] is None  # processing
    after = sorted(owner[j].get(f"/api/jobs/{j}").json()["position"] for j in places if j != claimed)
    assert after == [1, 2]


def test_queue_holds_20_jobs(client):
    assert config.MAX_QUEUED == 20
    for _ in range(20):
        assert upload(client, [("a.bin", b"x")]).status_code == 201
    r = upload(new_client(), [("b.bin", b"x")])  # the cap is global, not per browser
    assert r.status_code == 503 and r.json()["detail"] == "The queue is full. Try again when a job has finished."
    assert len(job_dirs()) == 20


def test_closed_switch(client, capsys):
    job_id = upload(client, [("a.bin", b"x")]).json()["id"]
    cli.main(["close"])
    cli.main(["close"])
    assert capsys.readouterr().out == "The service is closed.\n" * 2
    for r in (
        upload(client, [("a.bin", b"x")]),
        client.get(f"/api/jobs/{job_id}"),
        client.get(f"/api/jobs/{job_id}/zip"),
        client.delete(f"/api/jobs/{job_id}"),
        client.delete("/api/jobs"),
    ):
        assert (r.status_code, r.json()) == (503, {"detail": "closed"})
    r = new_client().get("/api/session")
    assert r.status_code == 200 and r.json() == {"open": False} and "session=" in r.headers["set-cookie"]

    cli.main(["open"])
    cli.main(["open"])
    assert capsys.readouterr().out == "The service is open.\n" * 2
    assert client.get("/api/session").json() == {"open": True}
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "queued"
    assert upload(client, [("a.bin", b"x")]).status_code == 201


def test_security_headers_on_every_response(client):
    for r in (client.get("/api/session"), client.get(f"/api/jobs/{uuid.uuid4()}")):
        assert r.headers["strict-transport-security"] == "max-age=31536000"
        assert r.headers["x-robots-tag"] == "noindex"
        csp = "default-src 'self'; img-src 'self' blob:; frame-ancestors 'none'"
        assert r.headers["content-security-policy"] == csp


def test_cross_origin_post_rejected(client):
    assert upload(client, [("a.bin", b"x")], headers={"Origin": "https://evil.example"}).status_code == 403
    no_origin = {k: v for k, v in client.headers.items() if k.lower() != "origin"}
    client.headers = no_origin
    assert upload(client, [("a.bin", b"x")]).status_code == 403


def test_missing_custom_header_rejected(client):
    del client.headers["X-LFT"]
    assert upload(client, [("a.bin", b"x")]).status_code == 403
    assert client.delete("/api/jobs").status_code == 403
    assert job_dirs() == []


def test_oversize_upload_rejected_by_content_length(client, monkeypatch):
    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 1000)
    assert upload(client, [("a.bin", b"x" * 2000)]).status_code == 413
    assert job_dirs() == []


def test_oversize_upload_rejected_while_streaming(client, monkeypatch):
    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 1000)
    body = (
        b'--B\r\nContent-Disposition: form-data; name="tool"\r\n\r\nimage\r\n'
        b'--B\r\nContent-Disposition: form-data; name="files"; filename="a.bin"\r\n\r\n'
        + b"x" * 5000 + b"\r\n--B--\r\n"
    )
    # A generator body is sent chunked, without Content-Length, so only the streaming count can catch it.
    chunks = (body[i : i + 512] for i in range(0, len(body), 512))
    r = client.post("/api/jobs", content=chunks, headers={"Content-Type": "multipart/form-data; boundary=B"})
    assert r.status_code == 413
    assert job_dirs() == []


def test_too_many_files_rejected(client, monkeypatch):
    monkeypatch.setattr(config, "MAX_FILES", 2)
    assert upload(client, [(f"{i}.bin", b"x") for i in range(3)]).status_code == 400
    assert job_dirs() == []
    assert upload(client, [(f"{i}.bin", b"x") for i in range(2)]).status_code == 201


def test_disk_budget_exceeded_rejected(client, monkeypatch):
    monkeypatch.setattr(config, "DISK_BUDGET_BYTES", 100_000)
    (config.JOBS_DIR / "leftover.bin").write_bytes(b"x" * 100_001)
    assert upload(client, [("a.bin", b"x")]).status_code == 507
    assert job_dirs() == []


def test_bad_tool_and_options_rejected(client):
    assert upload(client, [("a.bin", b"x")], tool="nope").status_code == 400
    assert upload(client, [("a.bin", b"x")], options="[1]").status_code == 400
    assert upload(client, [("a.bin", b"x")], options="{bad").status_code == 400
    assert job_dirs() == []


def test_image_job_end_to_end(client):
    png = image_bytes((300, 200))
    r = upload(client, [("photo ünï.png", png), ("../../etc/passwd", png)], options='{"format": "webp"}')
    assert r.status_code == 201
    job_id = r.json()["id"]
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "queued"
    assert sorted(os.listdir(config.JOBS_DIR / job_id / "in")) == ["0", "1"]  # names never reach the disk

    run_next_job()
    status = client.get(f"/api/jobs/{job_id}").json()
    assert status["status"] == "completed", status
    assert [o["name"] for o in status["outputs"]] == ["photo ünï.webp", "passwd.webp"]

    r = client.get(f"/api/jobs/{job_id}/files/0")
    assert Image.open(io.BytesIO(r.content)).format == "WEBP"
    assert r.headers["content-type"] == "image/webp"
    assert r.headers["content-disposition"].startswith("attachment;")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "no-store"
    assert "default-src 'self'" in r.headers["content-security-policy"]

    z = zipfile.ZipFile(io.BytesIO(client.get(f"/api/jobs/{job_id}/zip").content))
    assert z.read("photo ünï.webp") == r.content and z.namelist() == ["photo ünï.webp", "passwd.webp"]

    assert client.delete(f"/api/jobs/{job_id}").status_code == 204
    assert client.get(f"/api/jobs/{job_id}").status_code == 404
    assert job_dirs() == []


def test_bad_image_fails_job_with_message(client):
    job_id = upload(client, [("notes.jpg", b"not an image")], options='{"format": "png"}').json()["id"]
    run_next_job()
    status = client.get(f"/api/jobs/{job_id}").json()
    assert status["status"] == "failed" and "notes.jpg is not a supported image" in status["message"]


def test_hostile_pdf_name_cannot_inject_a_header(client):
    options = json.dumps({"page": "image", "name": 'résumé "x"\r\nSet-Cookie: a=b/../../y'})
    job_id = upload(client, [("a.png", image_bytes())], tool="images_to_pdf", options=options).json()["id"]
    run_next_job()
    assert client.get(f"/api/jobs/{job_id}").json()["outputs"][0]["name"] == "résumé xSet-Cookie a=b....y.pdf"
    r = client.get(f"/api/jobs/{job_id}/files/0")
    assert r.status_code == 200 and "set-cookie" not in r.headers
    assert r.headers["content-disposition"] == (
        "attachment; filename=\"r_sum_ xSet-Cookie a_b....y.pdf\"; "
        "filename*=UTF-8''r%C3%A9sum%C3%A9%20xSet-Cookie%20a%3Db....y.pdf"
    )


def _completed_job(client):
    job_id = upload(client, [("a.png", image_bytes())], options='{"format": "png"}').json()["id"]
    run_next_job()
    assert client.get(f"/api/jobs/{job_id}/files/0").status_code == 200
    return job_id


def test_symlinked_output_file_not_served(client):
    job_id = _completed_job(client)
    secret = config.STATE_DIR / "secret"
    secret.write_bytes(b"secret")
    out = config.JOBS_DIR / job_id / "out" / "0"
    out.unlink()
    out.symlink_to(secret)
    assert client.get(f"/api/jobs/{job_id}/files/0").status_code == 404
    assert client.get(f"/api/jobs/{job_id}/zip").status_code == 404


def test_symlinked_output_dir_not_served(client, tmp_path):
    job_id = _completed_job(client)
    (tmp_path / "evil").mkdir()
    (tmp_path / "evil" / "0").write_bytes(b"secret")
    out = config.JOBS_DIR / job_id / "out"
    (out / "0").unlink()
    out.rmdir()
    out.symlink_to(tmp_path / "evil")
    assert client.get(f"/api/jobs/{job_id}/files/0").status_code == 404


def test_fifo_output_not_served(client):
    job_id = _completed_job(client)
    out = config.JOBS_DIR / job_id / "out" / "0"
    out.unlink()
    os.mkfifo(out)
    assert client.get(f"/api/jobs/{job_id}/files/0").status_code == 404


def test_untrusted_job_row_is_sanitised(client):
    job_id = _completed_job(client)
    with db.jobs() as c:
        c.execute(
            "UPDATE jobs SET outputs = ? WHERE id = ?",
            ('[{"name": "x\\r\\nSet-Cookie: a=b", "size": 6, "type": "text/html"}]', job_id),
        )
    r = client.get(f"/api/jobs/{job_id}/files/0")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/octet-stream"
    assert "set-cookie" not in r.headers
    for garbage in ("{}", '[{"size": 1e999}]', "5"):
        with db.jobs() as c:
            c.execute("UPDATE jobs SET outputs = ? WHERE id = ?", (garbage, job_id))
        assert client.get(f"/api/jobs/{job_id}").json()["outputs"] == []


def test_cache_headers(client, tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "index-abc123.js").write_text("export {}")
    (tmp_path / "index.html").write_text("<!doctype html>")
    (tmp_path / "manifest.webmanifest").write_text("{}")
    site = FastAPI()
    site.mount("/", api.Site(directory=tmp_path, html=True))
    static = TestClient(api.security_headers(site))
    assert static.get("/assets/index-abc123.js").headers["cache-control"] == "public, max-age=31536000, immutable"
    assert static.get("/assets/gone.js").headers["cache-control"] == "no-cache"  # a 404 must not stick for a year
    assert static.get("/").headers["cache-control"] == "no-cache"
    assert static.get("/manifest.webmanifest").headers["cache-control"] == "no-cache"
    assert client.get("/api/session").headers["cache-control"] == "no-store"
    assert "strict-transport-security" in static.get("/").headers and "x-robots-tag" in static.get("/").headers


def test_unknown_page_gets_the_app_with_a_404(tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html>")
    (tmp_path / "icon.svg").write_text("<svg/>")
    site = FastAPI()
    site.mount("/", api.Site(directory=tmp_path, html=True))
    static = TestClient(site)
    assert (static.get("/").status_code, static.get("/").text) == (200, "<!doctype html>")
    assert (static.get("/icon.svg").status_code, static.get("/icon.svg").text) == (200, "<svg/>")
    r = static.get("/no/such/page")
    assert (r.status_code, r.text) == (404, "<!doctype html>")
    r = static.get("/api/nope")
    assert r.status_code == 404 and r.text != "<!doctype html>"
