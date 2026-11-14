# Backup and restore

## What to back up

Nothing in the app needs a backup. Uploads and results are temporary: they are deleted about 15 minutes after each job
finishes. The `state` volume holds only the `closed` flag file, which exists while the service is closed
(`python -m app.cli close`).

| What | Why |
|---|---|
| `.env` | Your limits |
| `compose.override.yaml`, if you made one | Your tunnel setup (see [deploy.md](deploy.md)) |

Copy these two files somewhere safe, for example with `cp .env env-backup`.

## Leftover `state.sqlite` from 1.1.0

An install that ran 1.1.0 still has `state.sqlite` in the `state` volume. Version 1.2.0 does not use it. The file holds
the old owner's password hash, so delete it by hand once a rollback to 1.1.0 is ruled out, together with its
`state.sqlite-wal` and `state.sqlite-shm` files if they exist:

```bash
docker compose exec web python -c "import os; [os.remove(p) for p in ('/data/state/state.sqlite', '/data/state/state.sqlite-wal', '/data/state/state.sqlite-shm') if os.path.exists(p)]"
```

## Restore

On a fresh install, follow the quick start up to and including `docker compose up -d --build`. Then put `.env` (and
`compose.override.yaml`, if you had one) back and apply it:

```bash
cp env-backup .env && docker compose up -d
```

## Warning

`docker compose down -v` deletes both volumes. That removes any running uploads and results, and the `closed` flag,
so a service that was closed opens again. Plain `docker compose down` keeps them.
