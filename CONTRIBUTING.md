# Contributing

## Setup

Needs [uv](https://docs.astral.sh/uv/) (it installs the Python version in `backend/.python-version`), Node.js with
npm, and optionally Ghostscript (`gs`) for the PDF compressor and its tests. Docker with Compose is only needed to
run the containers.

Build the frontend:

```bash
cd frontend && npm ci && npm run build
```

Run the backend without Docker, from `backend/`, with the data in a scratch directory and the built frontend served
by uvicorn:

```bash
export LFT_STATE_DIR=/tmp/lft/state LFT_JOBS_DIR=/tmp/lft/jobs LFT_STATIC_DIR=../frontend/dist
```

```bash
mkdir -p /tmp/lft/state /tmp/lft/jobs && uv run python -m app.cli create-owner
```

```bash
uv run python -m app.worker &
```

```bash
uv run uvicorn app.api:app --host 127.0.0.1 --port 8080
```

Then open `http://127.0.0.1:8080`. After a frontend change, run `npm run build` again and reload.

`npm run dev` (the Vite dev server on port 5173) does not work for logging in at the moment: its proxy does not
keep the `Host` header the backend's Origin check compares against, so POST requests get 403.

## Tests and checks

```bash
cd backend && uv run pytest
```

```bash
cd frontend && npm run build
```

`npm run build` runs the TypeScript check before building. Run both before committing.

## Conventions

- **Tool contract.** Each tool is a module in `backend/app/tools/` with a `run()` function, listed in `NAMES` in
  `backend/app/tools/__init__.py`. That file's docstring is the contract: where inputs are, how outputs are written
  (temp file, then `os.replace`), when to call `report()`, and `ToolError` for messages shown to the user.
- **Validation happens in the worker.** Web never opens or parses uploaded files; the tool, running in the worker's
  child process, checks each file by its content. Keep it that way: the worker has no network and cannot see the
  password database.
- **Web does not trust the jobs volume.** In `api.py`, build paths only from the job UUID and an index, and treat
  everything read from `jobs.sqlite` as untrusted.
- **No binary fixtures.** Tests create their images and PDFs at run time. Tests that need Ghostscript are skipped
  when `gs` is not installed.
- **Frontend.** React and TypeScript with plain CSS: no router, no UI kit, no CSS framework. Keep touch targets at
  about 44 px.
- New dependencies need a good reason; update `NOTICE` when you add one.

## Licence

LocalFileTools is licensed under AGPL-3.0 (see `LICENSE`). By contributing you agree that your contribution is
licensed under the same terms.
