# LocalFileTools — implementation plan

## Context

`~/Downloads/rough_plan.md` describes a private, self-hosted file converter (images→PDF, image convert/compress/resize, PDF compress) for a Linux Mint mini-PC, running in Docker Compose beside Navidrome behind an existing Cloudflare Tunnel. The project folder started empty. This plan turns the rough plan into concrete build decisions.

Decisions confirmed: **own login**, **HEIC/AVIF accepted as input**, **build on the mini-PC** (no CI/registry), **converter + compressor merged into one image tool** (3 cards).

## Changes to the rough plan

| Rough plan | Change | Why |
|---|---|---|
| ReportLab for PDF creation | **img2pdf** | Does A4/image-sized pages + margins in one call and embeds JPEGs without re-encoding. Far less layout code. |
| One SQLite DB | **Two SQLite files on two volumes**: `state` (owner, sessions — web only) and `jobs` (queue + files — web and worker) | Worker then truly can't read password hashes or sessions. |
| CSRF protection (unspecified) | `SameSite=Strict` cookie + Origin check + required custom header on mutating requests | Same-origin SPA; no token plumbing needed. |
| Worker "without internet" | `network_mode: none` | Possible because the queue is a SQLite file on a shared volume, not a network service. |
| Cancellation / timeout | Worker runs each job in a **child process** it can kill | Only reliable way to stop Pillow/Ghostscript mid-job; one mechanism covers cancel, 5-min timeout, and memory blowups. |
| Drag-and-drop reorder | **Up/down buttons** per image in v1 | HTML5 drag doesn't work on touch; buttons work on mobile and are accessible. Drag can come later. |
| Target size for all formats | Target size for **JPEG/WebP only** | PNG has no quality knob; UI says so instead of faking it. |
| File-type validation | Pillow's detected format / pikepdf open, not libmagic | Already-installed deps do the job. |
| Versioned images, rollback | Version = git tag; rollback = `git checkout <tag> && docker compose up -d --build` | Follows from build-on-server. |

Additions the rough plan lacks:
- **AGPL source link** in the UI footer (network-use clause).
- `Cache-Control: no-store` on all API/file responses + a strict CSP (`default-src 'self'`).
- Files stored on disk under job UUIDs only; original filenames kept in the DB and sanitized for `Content-Disposition`.
- Client IP for login rate limiting read from `CF-Connecting-IP` (trustworthy because only cloudflared can reach the port).
- Tests generate their synthetic images/PDFs at runtime — no binary fixtures in the repo.

Open item to confirm at deploy time: whether `cloudflared` runs as a host service or a container. Default assumes host service (web bound to `127.0.0.1:8080`); if it's a container, web joins its Docker network instead and publishes no port.

## Layout

```
compose.yaml            # web + worker, same image, different command
Dockerfile              # stage 1: node builds frontend; stage 2: python:3.13-slim + ghostscript
.env.example
backend/
  pyproject.toml, uv.lock
  app/
    config.py           # limits from env (rough plan §4)
    db.py               # schema for state.sqlite and jobs.sqlite, WAL mode
    auth.py             # argon2-cffi, sessions (token hash in DB), rate limit, CSRF/origin dependency
    api.py              # FastAPI routes + static frontend
    jobs.py             # create / claim / finish / cancel / delete
    worker.py           # claim loop, child process per job, cleanup timer
    cli.py              # `create-owner`
    tools/
      image.py          # convert + compress + resize
      images_to_pdf.py
      pdf_compress.py
  tests/
frontend/               # Vite + React + TS, plain CSS, no router/UI kit
docs/                   # architecture, deploy, update/rollback, backup
LICENSE (AGPL-3.0), NOTICE, README.md
```

Python deps: fastapi, uvicorn, python-multipart, argon2-cffi, pillow, pillow-heif, img2pdf, pikepdf. System: ghostscript.

## Backend design

**API** (all under `/api`; all but login/session require a session)
- `POST /login`, `POST /logout`, `GET /session`
- `POST /jobs` — multipart: `tool`, JSON `options`, files. Streams to disk, aborts past 50 MB / 30 files, rejects if 5 jobs already queued. Returns job id.
- `GET /jobs/{id}` — status, stage, input/output sizes, message
- `DELETE /jobs/{id}` — cancel if running, delete files
- `GET /jobs/{id}/files/{n}` and `GET /jobs/{id}/zip`

**Auth:** one owner row; `python -m app.cli create-owner` run via `docker compose run`. Cookie `HttpOnly; Secure; SameSite=Strict`, 7-day server-side session. 5 failed logins per IP → 15-minute lockout (in-memory counter).

**Queue:** `jobs` table (`id, tool, options, status, stage, created, finished, message`). Worker claims with one atomic `UPDATE … WHERE id = (SELECT … status='queued' ORDER BY created LIMIT 1) RETURNING`. On worker start, any `processing` → `failed`. Parent polls the row each second; kills the child on `cancelled` or at 5 min.

**Cleanup** (worker, on start + every 5 min): delete job dirs and rows finished > 1 h ago, plus orphan dirs. Temp budget: refuse new uploads when the jobs dir exceeds 2 GB; PDF compressor checks between attempts.

**Tools**
- *Image:* `Image.MAX_IMAGE_PIXELS` = 25 MP with the bomb warning made an error; check dimensions before decoding. `ImageOps.exif_transpose`, drop metadata, optional resize (keep ratio, Lanczos), flatten alpha onto white for JPEG. Target size = binary search on quality (≤ 7 encodes); if unreachable, return the smallest and report actual size.
- *Images → PDF:* normalise each image as above (plus user rotation), then `img2pdf.convert` with A4-or-image page size and margin. Order = upload order as arranged in the UI.
- *PDF compress:* pikepdf inspection — reject encrypted, signed (`/Sig`), AcroForm-with-fields, > 100 pages, or unparseable. Attempt 1: pikepdf structural save (object streams, recompress flate). Then Ghostscript `pdfwrite -dSAFER` ladder of (dpi, JPEG quality): (200, 75) → (150, 60) → (110, 45) → (72, 30). Presets low/medium/high = one rung each; target mode walks the ladder and stops at the first result ≤ target. Always return the smallest output that is smaller than the input; otherwise report "no improvement".

## Compose

- `web`: `uvicorn`, port `127.0.0.1:8080`, volumes `state` + `jobs`, healthcheck on `/api/session`.
- `worker`: `network_mode: none`, volume `jobs` only, `mem_limit: 2g`, `cpus: 2`, `tmpfs: /tmp`.
- Both: non-root user, `read_only: true`, `cap_drop: [ALL]`, `no-new-privileges`, `restart: unless-stopped`. No Docker socket, no host bind mounts.
- Backup = the `state` volume + `.env`.

## Frontend

Single page, view switched by state: login → 3 tool cards → tool screen (dropzone, settings, file list with up/down/rotate/remove for images→PDF) → progress → result (sizes, % saved, per-file download, ZIP, delete). Upload via `XMLHttpRequest` for progress; job status polled every second. No remote fonts, analytics, or third-party scripts.

## Build stages

1. **Foundation:** Dockerfile, compose, config, DBs, auth + CLI, upload validation, queue, worker child-process loop, cleanup, a no-op "echo" tool to prove the pipeline, login page.
2. **Image tools:** `image.py`, `images_to_pdf.py`, their UI screens.
3. **PDF compress:** inspection, ladder, UI.
4. **Release:** mobile polish, docs (deploy, update with Navidrome, rollback, backup), README + screenshots, LICENSE/NOTICE.

## Verification

- `pytest` in the backend: per-tool tests on generated files (dimensions, EXIF orientation, alpha→white, page count/order, target hit and unreachable, encrypted/signed/form PDF rejected, oversized pixel count rejected, PNG renamed `.jpg` handled by content); API tests for anonymous 401, expired session, cross-origin POST rejected, over-size upload, queue full; worker tests for cancel, timeout kill, restart recovery, cleanup after expiry.
- `docker compose up` locally, then drive the real UI in the browser pane at desktop and mobile widths for each tool end-to-end.
- From inside the worker container: confirm no network (`getent hosts example.com` fails) and that `state.sqlite` is not visible.
- On the mini-PC (manual): upload/download through the Tunnel, and Navidrome playback while a 100-page PDF compresses — then tune the §4 limits.

## Local status (this machine)

- Docker: not installed. Compose and container checks can't run here.
- Ghostscript: not installed. PDF-compress tests can't run here.
- Available: uv, Python 3.14, Node 26, git.
