# Architecture

## Containers

`compose.yaml` runs one image as two services:

- **web** — uvicorn serving the API (`backend/app/api.py`) and the built frontend. It accepts uploads, serves results,
  and checks the `closed` flag. Published on `127.0.0.1:8080` only.
- **worker** — `python -m app.worker`. Claims queued jobs, runs each tool, and deletes old jobs. It has no network
  (`network_mode: none`) and is limited to 2 GB RAM and 2 CPUs.

Both run as a non-root user with a read-only root filesystem, all capabilities dropped, `no-new-privileges`,
`init: true` and a 64 MB `/tmp` tmpfs. Neither mounts the Docker socket or any host directory.

## Storage: two volumes

| Volume | Contents | Mounted in |
|---|---|---|
| `state` | Only the `closed` flag file, present while the service is closed | web only |
| `jobs` | `jobs.sqlite` (the queue, with a SHA-256 hash of each browser's session token, never the token) and one directory per job with its uploads and results | web and worker |

The worker does not mount the state volume, so a compromised worker cannot change the `closed` flag. The jobs
database uses WAL mode. The state volume holds no database since 1.2.0; a `state.sqlite` left over from 1.1.0 is
not used.

## Job lifecycle

1. **Upload.** The browser sends one multipart request per job. Web rejects it up front if `Content-Length` is over
   the upload limit, the queue already holds `LFT_MAX_QUEUED` jobs, or the jobs volume is over its budget. It then
   streams the body straight to `jobs/<uuid>/in/<n>`, counting bytes and files, and aborts past the limits.
   Web does not open or parse the files.
2. **queued.** A row is inserted in `jobs.sqlite` with the tool, options and original filenames.
3. **processing.** The worker claims the oldest queued job with one atomic `UPDATE … RETURNING` and starts a child
   process (`python -m app.worker child <id>`) in its own process group, with `TMPDIR` set to the job directory.
   The child validates each input by content (Pillow's detected format, or a pikepdf open), runs the tool, and
   records progress and each usable result in the row.
4. **completed / failed / cancelled.** The parent checks the row every second. If the job is cancelled, or reaches
   `LFT_JOB_TIMEOUT_SECONDS`, it kills the whole process group (which includes Ghostscript). On a timeout the job
   completes with the best result recorded so far, or fails if there is none.
5. **Cleanup.** On start and every `LFT_CLEANUP_INTERVAL_SECONDS`, the worker deletes jobs that finished more than
   `LFT_RETENTION_SECONDS` ago, and job directories that have no row. When it starts, it also marks any job left
   in `processing` by a crash or restart as failed; queued jobs stay queued.

The browser polls `GET /api/jobs/<id>` every second. Deleting a job from the result screen removes it at once;
deleting a running job marks it cancelled, and the worker removes its files after the child is dead.

## Web treats the jobs volume as untrusted

A malicious upload could exploit a decoder and take over the worker, which can write anything to the jobs volume.
So web:

- never parses uploads (validation happens in the worker's child process);
- builds file paths only from the job UUID and an output index, never from strings stored in `jobs.sqlite`;
- opens results with `O_NOFOLLOW` at every path step and serves regular files only, so a planted symlink is
  refused;
- serves every result as `Content-Disposition: attachment`, with a content type from a fixed allowlist and
  `X-Content-Type-Options: nosniff`, and cleans filenames before putting them in the header.

All responses carry a strict Content Security Policy (`default-src 'self'`, no framing). API responses carry
`Cache-Control: no-store`.

## Anonymous sessions and request checks

- There is no login. Each browser gets an anonymous session cookie, `session`, with a random token, valid for 7 days.
  The jobs database stores only a SHA-256 hash of the token. A job can be read, downloaded or deleted only with the
  cookie of the browser that created it.
- The cookie is `HttpOnly; SameSite=Strict; Secure`. `Secure` is left off only for requests whose `Host` equals
  `LFT_LAN_HOST`, when that is set.
- Every request other than GET or HEAD must have an `Origin` whose host equals the `Host` header, and an `X-LFT: 1` header.
- While the `closed` flag file exists, every request except the session check answers "service closed". The flag is
  set and removed with `python -m app.cli close` and `python -m app.cli open`.

## Tools

Each tool is a module in `backend/app/tools/` with a `run()` function; the contract is in
`backend/app/tools/__init__.py`.

- **image** — opens by content with only the JPEG, PNG, WebP and AVIF decoders enabled; checks the pixel
  count before decoding; applies EXIF orientation; drops metadata; optional Lanczos resize; flattens transparency
  onto white for JPEG. Target size: binary search on quality, at most 7 encodes.
- **images_to_pdf** — the same loading and checks, plus the chosen rotation and then the chosen crop (fractions of
  the turned image, checked strictly, at least 8 × 8 pixels); the output name is sanitised here (no path parts,
  control characters, quotes or leading dots; at most 100 characters); transparency always flattened onto
  white. PNG inputs stay PNG inside the PDF, everything else becomes JPEG at quality 90. img2pdf builds the PDF.
- **pdf_compress** — inspects the PDF with pikepdf and rejects encrypted, signed, fillable-form, unreadable or
  too-long files. Attempt 1 is a pikepdf structural save; then Ghostscript `pdfwrite -dSAFER` with the `/printer`,
  `/ebook` and `/screen` presets. A strength runs one preset; a target walks them in order and stops at the first
  result at or under the target. The smallest result that beats the original is kept; if none does, the original
  is returned with a note. The jobs volume budget is re-checked between attempts.

The image tool also re-checks the jobs volume budget after each output it writes.

## Limits

See the limits table in the [README](../README.md#limits). Concurrency is fixed at one job at a time; the worker
and web memory and CPU limits are in `compose.yaml`.
