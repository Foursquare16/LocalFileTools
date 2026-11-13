# Architecture

## Containers

`compose.yaml` runs one image as two services:

- **web** — uvicorn serving the API (`backend/app/api.py`) and the built frontend. It holds the login and session
  database, accepts uploads, and serves results. Published on `127.0.0.1:8080` only.
- **worker** — `python -m app.worker`. Claims queued jobs, runs each tool, and deletes old jobs. It has no network
  (`network_mode: none`) and is limited to 2 GB RAM and 2 CPUs.

Both run as a non-root user with a read-only root filesystem, all capabilities dropped, `no-new-privileges`,
`init: true` and a 64 MB `/tmp` tmpfs. Neither mounts the Docker socket or any host directory.

## Storage: two SQLite files on two volumes

| Volume | Contents | Mounted in |
|---|---|---|
| `state` | `state.sqlite`: the owner (username, Argon2 hash) and sessions (SHA-256 of each token, never the token) | web only |
| `jobs` | `jobs.sqlite` (the queue) and one directory per job with its uploads and results | web and worker |

The split exists so that a compromised worker cannot read the password hash or session tokens: the state volume is
simply not mounted in the worker container. Both databases use WAL mode.

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
- opens results with `O_NOFOLLOW` at every path step and serves regular files only, so a planted symlink to
  `state.sqlite` is refused;
- serves every result as `Content-Disposition: attachment`, with a content type from a fixed allowlist and
  `X-Content-Type-Options: nosniff`, and cleans filenames before putting them in the header.

All responses carry a strict Content Security Policy (`default-src 'self'`, no framing). API responses carry
`Cache-Control: no-store`.

## Login and request checks

- One owner account, created or reset with `python -m app.cli create-owner`. No signup.
- Sessions last 7 days and are stored server-side. The cookie is `HttpOnly; Secure; SameSite=Strict`.
  `Secure` is left off only for requests whose `Host` equals `LFT_LAN_HOST`, when that is set.
- Every request other than GET or HEAD must have an `Origin` whose host equals the `Host` header, and an `X-LFT: 1` header.
- Five failed logins from one IP lock that IP out for 15 minutes. The IP is read from `CF-Connecting-IP`, which is
  only trustworthy because nothing but cloudflared can reach the web port. The counter is kept in memory and resets
  when web restarts.

## Tools

Each tool is a module in `backend/app/tools/` with a `run()` function; the contract is in
`backend/app/tools/__init__.py`.

- **image** — opens by content with only the JPEG, PNG, WebP, HEIF and AVIF decoders enabled; checks the pixel
  count before decoding; applies EXIF orientation; drops metadata; optional Lanczos resize; flattens transparency
  onto white for JPEG. Target size: binary search on quality, at most 7 encodes.
- **images_to_pdf** — the same loading and checks, plus the chosen rotation; transparency always flattened onto
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
