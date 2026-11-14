"""FastAPI routes under /api, plus the built frontend.

The worker can write anything to the jobs volume, so web treats it as untrusted: file paths come only from
the job UUID and an index, files are opened without following symlinks, and every download is an attachment
with an allowlisted content type.
"""

import json
import os
import re
import secrets
import shutil
import stat
import uuid
import zipfile
from urllib.parse import quote

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from python_multipart.exceptions import MultipartParseError
from python_multipart.multipart import MultipartParser, parse_options_header
from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import auth, config, db, jobs
from .tools import NAMES as TOOL_NAMES

CONTENT_TYPES = {"application/pdf", "image/jpeg", "image/png", "image/webp", "application/octet-stream"}
MAX_FIELD_BYTES = 64 * 1024
CHUNK = 1024 * 1024


def _session_cookie(scope, token: str) -> str:
    # Browsers drop a Secure cookie over plain HTTP, so the opt-in LAN address gets one without it.
    lan = bool(config.LAN_HOST) and Headers(scope=scope).get("host") == config.LAN_HOST
    flags = "HttpOnly; SameSite=Strict" + ("" if lan else "; Secure")
    return f"{auth.COOKIE}={token}; Max-Age={config.SESSION_SECONDS}; Path=/; {flags}"


def security_headers(app):
    """nosniff, CSP, HSTS and noindex on every response; no-store under /api, long caching for hashed /assets/,
    no-cache for the rest. An /api request without a well-formed session cookie gets a fresh one, also on errors."""

    async def wrapped(scope, receive, send):
        if scope["type"] != "http":
            return await app(scope, receive, send)
        new_token = None
        if scope["path"].startswith("/api"):
            token = Request(scope).cookies.get(auth.COOKIE)
            if not auth.well_formed(token):
                token = new_token = secrets.token_urlsafe(32)
            scope.setdefault("state", {})["session_token"] = token  # read by auth.session as request.state

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Content-Type-Options"] = "nosniff"
                headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' blob:; frame-ancestors 'none'"
                headers["Strict-Transport-Security"] = "max-age=31536000"
                headers["X-Robots-Tag"] = "noindex"
                if new_token:
                    headers.append("Set-Cookie", _session_cookie(scope, new_token))
                path = scope["path"]
                if path.startswith("/api"):
                    headers["Cache-Control"] = "no-store"
                elif path.startswith("/assets/") and message["status"] == 200:
                    # Vite puts a content hash in these names: a changed file gets a new name.
                    headers["Cache-Control"] = "public, max-age=31536000, immutable"
                else:
                    # index.html and the icons: revalidate, so a release never leaves a page pointing at old assets.
                    headers["Cache-Control"] = "no-cache"
            await send(message)

        await app(scope, receive, send_with_headers)

    return wrapped


class Site(StaticFiles):
    """The built frontend. An unknown path outside /api gets index.html with a 404, so the app shows "not found"."""

    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as e:
            if e.status_code != 404 or scope["path"].startswith("/api"):
                raise
            return FileResponse(os.path.join(self.directory, "index.html"), status_code=404)


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(security_headers)
public = APIRouter(prefix="/api", dependencies=[Depends(auth.check_csrf)])
# Everything else answers 503 while the service is closed.
service = APIRouter(prefix="/api", dependencies=[Depends(auth.check_csrf), Depends(auth.require_open)])


@public.get("/session")
def session():
    return {"open": not auth.closed_flag().exists()}


# --- uploads ---


async def _receive(request: Request, boundary: bytes, in_dir) -> tuple[dict[str, str], list[dict]]:
    """Stream the multipart body straight into in_dir/<n>, enforcing the size and file-count limits."""
    fields: dict[str, str] = {}
    files: list[dict] = []
    part: dict = {}
    header = [b"", b""]
    done = []

    def on_part_begin():
        part.clear()
        part["headers"] = {}

    def on_header_field(data, start, end):
        header[0] += data[start:end]

    def on_header_value(data, start, end):
        header[1] += data[start:end]

    def on_header_end():
        part["headers"][header[0].lower()] = header[1]
        header[:] = [b"", b""]

    def on_headers_finished():
        _, opts = parse_options_header(part["headers"].get(b"content-disposition", b""))
        name = opts.get(b"name", b"").decode(errors="replace")
        if b"filename" in opts and name == "files":
            if len(files) >= config.MAX_FILES:
                raise HTTPException(400, f"Too many files: at most {config.MAX_FILES} per job.")
            filename = opts[b"filename"].decode(errors="replace").replace("\\", "/").rsplit("/", 1)[-1]
            part["file"] = open(in_dir / str(len(files)), "xb")
            files.append({"name": filename[:255], "size": 0})
        elif name in ("tool", "options") and b"filename" not in opts:
            part["field"], part["value"] = name, bytearray()
        else:
            raise HTTPException(400, "Unexpected form field.")

    def on_part_data(data, start, end):
        chunk = data[start:end]
        if "file" in part:
            part["file"].write(chunk)
            files[-1]["size"] += len(chunk)
        else:
            part["value"] += chunk
            if len(part["value"]) > MAX_FIELD_BYTES:
                raise HTTPException(400, "Form field too large.")

    def on_part_end():
        if "file" in part:
            part.pop("file").close()
        else:
            fields[part["field"]] = part["value"].decode(errors="replace")

    parser = MultipartParser(boundary, {
        "on_part_begin": on_part_begin,
        "on_header_field": on_header_field,
        "on_header_value": on_header_value,
        "on_header_end": on_header_end,
        "on_headers_finished": on_headers_finished,
        "on_part_data": on_part_data,
        "on_part_end": on_part_end,
        "on_end": lambda: done.append(True),
    })
    received = 0
    try:
        async for chunk in request.stream():
            received += len(chunk)
            if received > config.MAX_UPLOAD_BYTES:
                raise HTTPException(413, f"Upload too large: at most {config.MAX_UPLOAD_BYTES // config.MB} MB per job.")
            parser.write(chunk)
    except MultipartParseError:
        raise HTTPException(400, "Malformed upload.")
    finally:
        if "file" in part:
            part["file"].close()
    if not done:
        raise HTTPException(400, "Incomplete upload.")
    return fields, files


@service.post("/jobs", status_code=201)
async def create_job(request: Request, session: str = Depends(auth.session)):
    # Not FastAPI's UploadFile: that spools the whole body to /tmp before the handler runs.
    try:
        length = int(request.headers.get("content-length", 0))
    except ValueError:
        raise HTTPException(400, "Bad Content-Length.")
    if length > config.MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"Upload too large: at most {config.MAX_UPLOAD_BYTES // config.MB} MB per job.")
    ctype, params = parse_options_header(request.headers.get("content-type", ""))
    boundary = params.get(b"boundary")
    if ctype != b"multipart/form-data" or not boundary:
        raise HTTPException(400, "Expected multipart/form-data.")
    with db.jobs() as c:
        if jobs.queued_count(c) >= config.MAX_QUEUED:
            raise HTTPException(503, "The queue is full. Try again when a job has finished.")
    if jobs.disk_usage() + length > config.DISK_BUDGET_BYTES:
        raise HTTPException(507, "The server's temporary storage is full. Try again later.")

    job_id = str(uuid.uuid4())
    job_dir = config.JOBS_DIR / job_id
    (job_dir / "in").mkdir(parents=True)
    try:
        fields, files = await _receive(request, boundary, job_dir / "in")
        tool = fields.get("tool", "")
        if tool not in TOOL_NAMES:
            raise HTTPException(400, "Unknown tool.")
        try:
            options = json.loads(fields.get("options", "{}"))
        except ValueError:
            options = None
        if not isinstance(options, dict):
            raise HTTPException(400, "Options must be a JSON object.")
        if not files:
            raise HTTPException(400, "No files uploaded.")
        with db.jobs() as c:
            if not jobs.create(c, job_id, session, tool, options, files):
                raise HTTPException(503, "The queue is full. Try again when a job has finished.")
    except BaseException:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise
    return {"id": job_id}


# --- job status and downloads (jobs.sqlite and job dirs are worker-writable: trust nothing in them) ---


def _clean_name(value) -> str:
    name = "".join(ch for ch in str(value) if ch.isprintable() and ch not in "/\\")[:200].strip()
    return name or "file"


def _text(value) -> str | None:
    return None if value is None else str(value)[:1000]


def _file_list(raw) -> list[dict]:
    try:
        return [
            {
                "name": _clean_name(item.get("name")),
                "size": int(item.get("size", 0)),
                "type": item.get("type") if item.get("type") in CONTENT_TYPES else "application/octet-stream",
            }
            for item in json.loads(raw)[:100]
        ]
    except Exception:  # the row is worker-writable: any shape of garbage means "no files"
        return []


def _job_id(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise HTTPException(404, "No such job.")


def _job_row(value: str, session: str):
    job_id = _job_id(value)
    with db.jobs() as c:
        row = jobs.get_own(c, job_id, session)
    if row is None:
        raise HTTPException(404, "No such job.")
    return job_id, row


def _outputs_of_completed(value: str, session: str):
    job_id, row = _job_row(value, session)
    outputs = _file_list(row["outputs"])
    if row["status"] != "completed" or not outputs:
        raise HTTPException(404, "No result to download.")
    return job_id, outputs


def _open_output(job_id: str, n: int):
    """Open <JOBS_DIR>/<job_id>/out/<n>, refusing a symlink at every step and anything but a regular file."""
    fd = os.open(config.JOBS_DIR, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for name, flags in ((job_id, os.O_DIRECTORY), ("out", os.O_DIRECTORY), (str(n), os.O_NONBLOCK)):
            next_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
    except OSError:
        os.close(fd)
        raise HTTPException(404, "File not found.")
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise HTTPException(404, "File not found.")
    return os.fdopen(fd, "rb")


def _disposition(name: str) -> str:
    ascii_name = re.sub(r"[^A-Za-z0-9._ -]", "_", name)
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name, safe='')}"


def _stream(f):
    with f:
        while chunk := f.read(CHUNK):
            yield chunk


@service.get("/jobs/{job_id}")
def job_status(job_id: str, session: str = Depends(auth.session)):
    job_id, row = _job_row(job_id, session)
    with db.jobs() as c:
        position = jobs.position(c, row)
    return {
        "id": job_id,
        "tool": _text(row["tool"]),
        "status": _text(row["status"]),
        "stage": _text(row["stage"]),
        "message": _text(row["message"]),
        "position": position,
        "inputs": [{"name": f["name"], "size": f["size"]} for f in _file_list(row["inputs"])],
        "outputs": _file_list(row["outputs"]),
    }


def _delete(job_id: str, session: str) -> str | None:
    with db.jobs() as c:
        result = jobs.delete(c, job_id, session)
    if result == "deleted":
        shutil.rmtree(config.JOBS_DIR / job_id, ignore_errors=True)  # rmtree never follows symlinks
    return result


@service.delete("/jobs/{job_id}", status_code=204)
def delete_job(job_id: str, session: str = Depends(auth.session)):
    if _delete(_job_id(job_id), session) is None:
        raise HTTPException(404, "No such job.")


@service.delete("/jobs", status_code=204)
def delete_my_jobs(session: str = Depends(auth.session)):
    """Every job of this browser, each as the single delete does it."""
    with db.jobs() as c:
        ids = jobs.ids_of(c, session)
    for value in ids:
        try:
            job_id = _job_id(str(value))  # the row is worker-writable: only a real UUID names a folder
        except HTTPException:
            continue
        _delete(job_id, session)


@service.get("/jobs/{job_id}/files/{n}")
def download(job_id: str, n: int, session: str = Depends(auth.session)):
    job_id, outputs = _outputs_of_completed(job_id, session)
    if not 0 <= n < len(outputs):
        raise HTTPException(404, "File not found.")
    f = _open_output(job_id, n)
    return StreamingResponse(
        _stream(f),
        media_type=outputs[n]["type"],
        headers={"Content-Disposition": _disposition(outputs[n]["name"]), "Content-Length": str(os.fstat(f.fileno()).st_size)},
    )


class _Sink:
    """Write-only buffer for ZipFile; without seek/tell, ZipFile streams entries with data descriptors."""

    def __init__(self):
        self.data = bytearray()

    def write(self, b):
        self.data += b
        return len(b)

    def flush(self):
        pass

    def take(self) -> bytes:
        out = bytes(self.data)
        self.data.clear()
        return out


def _zip(files, outputs):
    sink, seen = _Sink(), set()
    with zipfile.ZipFile(sink, "w") as z:  # stored, not deflated: results are already compressed formats
        for n, (f, out) in enumerate(zip(files, outputs)):
            name = out["name"] if out["name"] not in seen else f"{n + 1}-{out['name']}"
            seen.add(name)
            with f, z.open(name, "w") as entry:
                while chunk := f.read(CHUNK):
                    entry.write(chunk)
                    yield sink.take()
    yield sink.take()


@service.get("/jobs/{job_id}/zip")
def download_zip(job_id: str, session: str = Depends(auth.session)):
    job_id, outputs = _outputs_of_completed(job_id, session)
    files = [_open_output(job_id, n) for n in range(len(outputs))]  # 404 before streaming if any is unsafe
    return StreamingResponse(
        _zip(files, outputs), media_type="application/zip", headers={"Content-Disposition": _disposition("results.zip")}
    )


app.include_router(public)
app.include_router(service)
if config.STATIC_DIR.is_dir():
    app.mount("/", Site(directory=config.STATIC_DIR, html=True), name="static")
