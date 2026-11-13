# Deploying beside Navidrome behind a Cloudflare Tunnel

This assumes a Linux server that already runs Navidrome in its own Compose project and already has a Cloudflare
Tunnel. LocalFileTools is a separate Compose project: it shares no volumes with Navidrome and mounts nothing of it.

Placeholders used below (replace them with your own values):

| Placeholder | Meaning |
|---|---|
| `files.example.com` | the public hostname you give LocalFileTools |
| `~/LocalFileTools` | where you clone this repository |
| `/path/to/navidrome` | the directory of Navidrome's Compose project |
| `navidrome_default` | the Docker network cloudflared is on (case B only) |

## 1. Install and create the owner

```bash
git clone git@github.com:Foursquare16/LocalFileTools.git ~/LocalFileTools
```

```bash
cd ~/LocalFileTools && cp .env.example .env
```

Adjust the limits in `.env` if you want (see the README), then:

```bash
docker compose up -d --build
```

```bash
docker compose exec web python -m app.cli create-owner
```

To reset the password later, run `create-owner` again. It replaces the owner (username and password) and signs out
every session.

Check that it answers on the server:

```bash
curl -s http://127.0.0.1:8080/api/session
```

It should print `{"authenticated":false}`.

## 2. Connect it to the tunnel

Find out how cloudflared runs on the server. If `systemctl status cloudflared` shows a running service, use case A.
If it is a container (`docker ps --format '{{.Names}} {{.Image}}' | grep cloudflared` lists it), use case B.

### Case A: cloudflared as a host service

Nothing changes in `compose.yaml`: web is published on `127.0.0.1:8080`, reachable from the server only.

Add a route for the new hostname to the tunnel, pointing at `http://127.0.0.1:8080`.

If the tunnel is configured in the Cloudflare dashboard, add a public hostname there: `files.example.com`, service
type HTTP, URL `127.0.0.1:8080`.

If the tunnel uses a local config file (often `/etc/cloudflared/config.yml`), add an ingress rule above the final
catch-all rule, keeping your Navidrome rule as it is:

```yaml
ingress:
  # ... your existing Navidrome rule ...
  - hostname: files.example.com
    service: http://127.0.0.1:8080
  - service: http_status:404
```

Then create the DNS record and restart cloudflared:

```bash
cloudflared tunnel route dns <tunnel-name> files.example.com
```

```bash
sudo systemctl restart cloudflared
```

If port 8080 is already taken on the server, publish another port with a `compose.override.yaml` next to
`compose.yaml` (Compose reads it automatically, and git ignores it), and use that port in the tunnel route:

```yaml
services:
  web:
    ports: !override ["127.0.0.1:8081:8080"]
```

### Case B: cloudflared as a container

web joins cloudflared's Docker network and publishes no port. First find that network's name:

```bash
docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' <cloudflared-container-name>
```

Create `compose.override.yaml` next to `compose.yaml`, with that name in place of `navidrome_default`:

```yaml
services:
  web:
    ports: !reset []
    networks:
      cloudflared:
        aliases: [localfiletools]

networks:
  cloudflared:
    external: true
    name: navidrome_default
```

Apply it:

```bash
docker compose up -d
```

`docker compose ps` should now show no port for web. In the tunnel, route `files.example.com` to
`http://localfiletools:8080` (in the dashboard: service type HTTP, URL `localfiletools:8080`; in a config file:
`service: http://localfiletools:8080`).

In this case any container on that network (Navidrome included) can reach web directly and could set its own
`CF-Connecting-IP` header, which the login rate limit trusts. The worker is not affected; it has no network at all.

The network must exist before LocalFileTools starts, so start Navidrome's project first after changes to it.

## 3. Check the Host header

The app rejects any POST whose `Origin` does not match the `Host` header. Through the tunnel this works only if
cloudflared passes the public hostname through as `Host`, which is its default. Do not set `httpHostHeader` for this
route.

To check, from any machine:

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST -H 'Origin: https://files.example.com' -H 'X-LFT: 1' https://files.example.com/api/logout
```

`204` means the Host header arrives intact. `403` means it does not, and logging in will fail with "Cross-site request
rejected."

Then open `https://files.example.com`, log in, and run one small job.

Cloudflare features that add or rewrite scripts in pages can conflict with the page's Content Security Policy
(`default-src 'self'`). If the page works on the server but is blank or broken through the tunnel, check those
settings for this hostname first.

## Commands not run while writing this

No tunnel was available, so the `cloudflared` and `systemctl` commands and the dashboard steps were not run, and the
Host header check was run against `http://127.0.0.1:8080` rather than through a tunnel. Everything else on this page
was run against this repository's `compose.yaml`, with a test network standing in for cloudflared's.
