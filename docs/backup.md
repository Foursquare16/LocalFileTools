# Backup and restore

## What to back up

| What | Why |
|---|---|
| The `state` volume (`state.sqlite`) | The owner account (username and Argon2 password hash) and the active sessions |
| `.env` | Your limits |
| `compose.override.yaml`, if you made one | Your tunnel setup (see [deploy.md](deploy.md)) |

Uploaded files and results (the `jobs` volume) are never backed up. They are deleted about an hour after each job
anyway, and losing them loses nothing that matters.

There is not much to lose: without a backup you can recreate the owner with `create-owner` and copy
`.env.example` again. The backup saves you those steps.

Keep backups off the server. `state.sqlite` contains the password hash.

## Back up

Run these in the clone (`~/LocalFileTools` in the deploy guide). The first command takes a consistent copy of the
database through SQLite's backup API while the app keeps running, and writes it to `state-backup.sqlite` in the
current directory.

```bash
docker compose exec -T web python -c "import sqlite3, sys; d = sqlite3.connect(':memory:'); sqlite3.connect('/data/state/state.sqlite').backup(d); sys.stdout.buffer.write(d.serialize())" > state-backup.sqlite
```

```bash
cp .env env-backup
```

Move `state-backup.sqlite` and `env-backup` somewhere safe.

## Restore

On a fresh install, follow the quick start up to and including `docker compose up -d --build`, but skip
`create-owner`. Put `.env` back first:

```bash
cp env-backup .env
```

With the stack running and `state-backup.sqlite` in the current directory:

```bash
docker compose exec -T web python -c "import os, sqlite3, sys; p = '/data/state/restore.sqlite'; open(p, 'wb').write(sys.stdin.buffer.read()); src = sqlite3.connect(p); src.backup(sqlite3.connect('/data/state/state.sqlite')); src.close(); os.remove(p)" < state-backup.sqlite
```

This replaces the owner and sessions with those from the backup. Log in with the backed-up username and password.

## Warning

`docker compose down -v` deletes both volumes, including the owner account. Plain `docker compose down` keeps
them.

## Commands run while writing this

The backup and restore commands were run against a running stack: back up, reset the owner with `create-owner`,
restore, then log in again with the original owner.
