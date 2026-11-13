# Updating and rolling back

The image is built on the server from the checked-out source; there is no registry. A version is a git tag.

Restarting the worker fails any job that is processing at that moment ("Interrupted by a restart."); queued jobs
stay queued. Update when nothing is running. Volumes, the owner account and `.env` are kept.

## Update to the latest commit

```bash
cd ~/LocalFileTools
```

```bash
git pull
```

```bash
docker compose up -d --build
```

Then check that both containers are up and web is healthy:

```bash
docker compose ps
```

Each rebuild leaves the previous image behind, unused. To remove such images:

```bash
docker image prune -f
```

## Versions

Tag a release where you develop and push the tag:

```bash
git tag v1.0.0 && git push origin v1.0.0
```

On the server, deploy a specific version:

```bash
git fetch --tags && git checkout v1.0.0 && docker compose up -d --build
```

## Roll back

Check out the previous tag and rebuild:

```bash
git checkout <tag> && docker compose up -d --build
```

This leaves git in a detached-HEAD state. To return to the latest version later:

```bash
git checkout master && git pull && docker compose up -d --build
```

The databases are created with `CREATE TABLE IF NOT EXISTS` and there are no migrations yet, so the current
versions can be rolled back and forward over the same volumes.

## Updating together with Navidrome

The two projects are independent, so either can be updated alone. When doing both, update Navidrome first, then
LocalFileTools. For an image-based Navidrome project that is (not run while writing this):

```bash
cd /path/to/navidrome && docker compose pull && docker compose up -d
```

If cloudflared runs as a container in that project (case B in [deploy.md](deploy.md)), use `up -d` there, not
`down`: LocalFileTools' web container is attached to that project's network, and Docker refuses to remove a network
that still has containers on it. Recreating the cloudflared container briefly interrupts both sites.

`compose.override.yaml` and `.env` are not tracked by git, so `git pull` and `git checkout` leave them alone.

## Commands run while writing this

Everything above except `git push` and the Navidrome command was run on a test clone: tagging, checking out an
older tag and rebuilding (the old frontend was served), then returning to `master` and rebuilding.
