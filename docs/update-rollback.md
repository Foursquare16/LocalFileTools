# Updating and rolling back

A version is a git tag. The server either builds the image from the checked-out source (the first sections below)
or pulls the image published for each tag (see [Using the published image](#using-the-published-image)).

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

## Using the published image

Pushing a tag like `v1.0.0` makes GitHub build and publish `ghcr.io/foursquare16/localfiletools:1.0.0`
(`.github/workflows/image.yml`; the build takes a few minutes). A server that uses this image needs no source
checkout and never builds.

In `compose.yaml`, under `x-hardening`, remove `build: .` and pin the version:

```yaml
  image: ghcr.io/foursquare16/localfiletools:1.0.0
```

The two services can also be copied into another Compose project's file, for example the one that runs Navidrome.
Set values from `.env.example` under `environment:` there if you do not want a second `.env`.

To update or roll back, change the version number, then:

```bash
docker compose pull
```

```bash
docker compose up -d
```

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

The published image was checked on a real server: tag `v1.0.0` was built by GitHub, pulled without a login, and run
from another Compose project's file with the existing volumes. Editing this repository's `compose.yaml` to use the
image was not run.
