# LocalFileTools — implementation plan

## 1. What we're building

A private, self-hosted file converter with its own interface, backend, login and processing workflow. Local libraries handle the file formats; no external conversion APIs. It runs on a Linux Mint mini-PC in Docker Compose, beside Navidrome, behind the existing Cloudflare Tunnel.

**Tools (3 cards)**

| Tool | Features |
|---|---|
| Images → PDF | Upload several images, reorder and rotate them, A4 or image-sized pages, margins, one PDF out |
| Image tool | Convert between JPEG, PNG and WebP; resize keeping proportions; quality or best-effort target size |
| PDF compressor | Compression strength or a target such as 500 KB; returns the smallest successful result |

Inputs accepted for images: JPEG, PNG, WebP, HEIC, AVIF. Outputs: JPEG, PNG, WebP.

**Out of scope for this release:** audio, video, OCR, PDF editing, resizing existing PDF page dimensions, public signup, multiple users.

**Decisions confirmed:** own login · HEIC/AVIF accepted as input · build on the mini-PC (no CI or registry) · converter and compressor merged into one image tool · React/TypeScript frontend.

**Privacy boundary (accepted):** files are processed and stored only on the server, but Cloudflare can decrypt proxied traffic. No analytics, remote fonts or third-party scripts.

## 2. Limits (configurable via env)

| Setting | Default |
|---|---|
| Concurrent processing | 1 job |
| Queued jobs | 5 |
| Upload size | 50 MB **total per job**, one request per job (stays under Cloudflare's 100 MB body cap) |
| Images | 30 per job; **60 megapixels** each (covers 48–50 MP phone cameras) |
| PDF length | 100 pages |
| Processing timeout | 5 minutes per job, including all compression attempts |
| Worker resources | 2 GB RAM, 2 CPU cores |
| Temporary storage budget | 2 GB for the jobs volume |
| File retention | Deleted within 1 hour of completion/failure |
| Cleanup | On worker start and every 5 minutes |

Starting values, to be tuned on the mini-PC while Navidrome is streaming.

## 3. Layout

```
compose.yaml            # web + worker, same image, different command
Dockerfile              # stage 1: node builds frontend; stage 2: python:3.13-slim + ghostscript
.env.example
backend/
  .python-version       # 3.13, so local uv matches the image
  pyproject.toml, uv.lock
  app/
    config.py           # limits from env (§2)
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
LICENSE (AGPL-3.0), NOTICE, README.md, CONTRIBUTING.md
```

Python deps: fastapi, uvicorn, python-multipart, argon2-cffi, pillow (≥ 11.3, native AVIF), pillow-heif (HEIC), img2pdf, pikepdf. System: ghostscript. Confirm the AVIF/HEIC split against current releases when locking.

## 4. Backend design

### API (all under `/api`; everything except login/session needs a session)

- `POST /login`, `POST /logout`, `GET /session`
- `POST /jobs` — multipart: `tool`, JSON `options`, files. Returns job id.
- `GET /jobs/{id}` — status, stage, input/output sizes, message
- `DELETE /jobs/{id}` — cancel if running, delete files
- `GET /jobs/{id}/files/{n}` and `GET /jobs/{id}/zip`

Job states: `queued → processing → completed | failed | cancelled`. The browser polls status; processing never holds the upload request open.

### Uploads

- Reject up front when `Content-Length` exceeds the limit, then parse `request.stream()` with python-multipart directly, counting bytes and writing each file straight into the job dir. Abort past 50 MB or 30 files. FastAPI's `UploadFile` is not used: it spools the whole body to `/tmp` before the handler runs.
- Reject when 5 jobs are already queued, or when the jobs volume is over the 2 GB budget.
- Validate by content, not extension: Pillow's detected format for images, a pikepdf open for PDFs. This runs in the worker's child process, never in web, so untrusted files are not parsed in the container that holds the password database.
- Files live on disk under the job UUID and an index only. Original filenames are kept in the DB and sanitized at response time for `Content-Disposition`.

### Auth

- One owner row, created with `python -m app.cli create-owner` via `docker compose run`. No signup, no email. Password reset = run the command again.
- Argon2 hashes. 7-day server-side session; only the token hash is stored. Cookie `HttpOnly; Secure; SameSite=Strict`
  (`Secure` is left off only for the opt-in plain-HTTP LAN address, `LFT_LAN_HOST`).
- CSRF: `SameSite=Strict` + Origin check + a required custom header on mutating requests.
- Login rate limit: 5 failures per IP → 15-minute lockout (in-memory). IP read from `CF-Connecting-IP`, trustworthy because only cloudflared can reach the port.

### Storage

Two SQLite files on two volumes, so the worker cannot read password hashes or sessions:

- `state` volume — `state.sqlite` (owner, sessions). Web only.
- `jobs` volume — `jobs.sqlite` (queue) + job dirs. Web and worker.

### Web does not trust the jobs volume

A malicious file could compromise the worker, which can write anything to the jobs volume. So web:

- builds file paths only from the job UUID and index, never from strings in the DB;
- opens downloads with `O_NOFOLLOW` and serves regular files only (no symlink to `state.sqlite`);
- serves every output as `Content-Disposition: attachment`, with a content type from a fixed allowlist and `X-Content-Type-Options: nosniff`.

All API and file responses carry `Cache-Control: no-store`; pages get a strict CSP (`default-src 'self'`).

### Queue and worker

- `jobs` table: `id, tool, options, status, stage, created, finished, message`, plus the recorded best output so far.
- Claim: one atomic `UPDATE … WHERE id = (SELECT … status='queued' ORDER BY created LIMIT 1) RETURNING`.
- On worker start, any `processing` row → `failed`. Queued jobs stay queued.
- Each job runs in a **child process in its own process group** (`start_new_session=True`). The parent polls the row each second and kills the whole group (`killpg`) on `cancelled` or at 5 minutes, which also takes down Ghostscript, a grandchild.
- The child records each finished attempt (file + size) in the row as it goes. On timeout the parent completes the job with the best recorded output and says the target was not reached; it fails the job only when nothing was produced.
- `TMPDIR` for the child is the job dir on the jobs volume, so Ghostscript temp files count against the disk budget instead of the container's RAM.
- The worker deletes a cancelled job's files after the child is dead; web only flips the status.

### Cleanup

Worker, on start and every 5 minutes: delete job dirs and rows finished more than 1 hour ago, plus orphan dirs. The PDF compressor re-checks the 2 GB budget between attempts.

## 5. Tools

### Image tool

- `Image.MAX_IMAGE_PIXELS` = 60 MP with the decompression-bomb warning turned into an error; check dimensions before decoding.
- `ImageOps.exif_transpose`, drop metadata, optional resize (keep ratio, Lanczos), flatten alpha onto white for JPEG.
- Dimensions stay unchanged during target-size compression unless resize is selected.
- Target size is offered for **JPEG and WebP only** (PNG has no quality knob; the UI says so). Binary search on quality, at most 7 encodes. If the target is unreachable, return the smallest result and report its real size.

### Images → PDF

- Normalise each image as above, plus the user's rotation.
- Intermediate format: PNG inputs stay PNG (lossless, for screenshots and text); everything else becomes JPEG at quality 90. **Alpha is always flattened onto white**, because img2pdf rejects alpha channels.
- `img2pdf.convert` with A4-or-image page size and margin. Order = the order arranged in the UI.

### PDF compressor

- Inspect with pikepdf. Reject encrypted, signed (`/Sig`), AcroForm-with-fields, more than 100 pages, or unparseable.
- Attempt 1: pikepdf structural save (object streams, recompress flate).
- Then Ghostscript `pdfwrite -dSAFER` using its built-in presets as the ladder: `/printer` (300 dpi) → `/ebook` (150 dpi) → `/screen` (72 dpi). Text and vectors are kept, pages are never rasterised.
- Strength low / medium / high = one rung each. Target mode walks the ladder and stops at the first result ≤ target.
- Always return the smallest output that beats the input; otherwise report "no improvement". Sizes are best-effort: never promise an exact size or lossless compression.

## 6. Compose

- `web`: uvicorn on `127.0.0.1:8080`, volumes `state` + `jobs`, healthcheck = a Python one-liner hitting `/api/session` (the slim image has no curl).
- `worker`: `network_mode: none`, volume `jobs` only, `mem_limit: 2g`, `cpus: 2`.
- Both: non-root user, `read_only: true`, `cap_drop: [ALL]`, `no-new-privileges`, `init: true` (reaps killed children), `tmpfs: /tmp` capped at 64 MB, `restart: unless-stopped`. No Docker socket, no host bind mounts, nothing of Navidrome's mounted.
- Version = git tag. Rollback = `git checkout <tag> && docker compose up -d --build`.
- Backup = the `state` volume + `.env`. Uploaded files are never backed up.

**Open item at deploy time:** whether `cloudflared` runs as a host service or a container. Default assumes a host service (web on `127.0.0.1:8080`). If it is a container, web joins its Docker network and publishes no port.

## 7. Frontend

Single page, view switched by state: login → 3 tool cards → tool screen → progress → result.

- Tool screen: dropzone, settings, and for Images → PDF a file list with up/down/rotate/remove. Up/down buttons instead of drag reorder in v1: HTML5 drag does not work on touch.
- Upload via `XMLHttpRequest` for progress; job status polled every second.
- Result: original and result sizes, % saved, per-file download, ZIP, delete. Cancel is available while processing.
- Footer links to the source (AGPL network-use clause).

## 8. Build stages

1. **Foundation:** Dockerfile, compose, config, DBs, auth + CLI, streamed upload validation, queue, worker child-process loop, cleanup, a no-op "echo" tool to prove the pipeline, login page.
2. **Image tools:** `image.py`, `images_to_pdf.py`, their UI screens.
3. **PDF compress:** inspection, ladder, UI.
4. **Release:** mobile polish, docs (deploy, updating together with Navidrome, rollback, backup), README + screenshots, LICENSE/NOTICE, CONTRIBUTING, `.env.example`.

## 9. Verification

- **`pytest`** (tests generate their images/PDFs at runtime; no binary fixtures in the repo):
  - tools — dimensions, EXIF orientation, alpha → white, page count and order, target hit and unreachable, PNG renamed `.jpg` handled by content, oversized pixel count rejected, encrypted/signed/form PDF rejected, already-compressed PDF reports "no improvement";
  - API — anonymous 401, expired session, cross-origin POST rejected, over-size upload, too many files, queue full, symlink in a job dir not served;
  - worker — cancel, timeout kills the whole process group, timeout returns best-so-far, restart recovery, cleanup after expiry, disk budget exceeded.
  - Ghostscript tests skip when `gs` is missing.
- **Container:** `docker compose up`, then drive the real UI at desktop and mobile widths for each tool end to end. From inside the worker: no network (`getent hosts example.com` fails) and `state.sqlite` not visible.
- **Mini-PC (manual):** upload/download through the Tunnel with scanned and text PDFs, and Navidrome playback while a 100-page PDF compresses — then tune §2.

## 10. Local status (dev machine)

- Available: uv, Python 3.14, Node 26, git.
- **Missing: Docker and Ghostscript.** Until they are installed here, container checks and PDF-compress tests only run on the mini-PC.
