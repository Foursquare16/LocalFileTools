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

## 1. Install

```bash
git clone https://github.com/Foursquare16/LocalFileTools.git ~/LocalFileTools
```

```bash
cd ~/LocalFileTools && cp .env.example .env
```

Adjust the limits in `.env` if you want (see the README), then:

```bash
docker compose up -d --build
```

Check that it answers on the server:

```bash
curl -s http://127.0.0.1:8080/api/session
```

It should print `{"open":true}`.

## 2. Connect it to the tunnel

Find out how cloudflared runs on the server. If `systemctl status cloudflared` shows a running service, use case A.
If it is a container (`docker ps --format '{{.Names}} {{.Image}}' | grep cloudflared` lists it), use case B.
Case C works with either and also opens the app to your LAN.

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

The `!override` and `!reset` tags used below need Docker Compose 2.24 or newer (`docker compose version`).

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

The network must exist before LocalFileTools starts, so start Navidrome's project first after changes to it.

### Case C: published on the LAN, with or without a tunnel

Use this to open the app directly from other devices on your network, for example when the tunnel or your internet
connection is down. A cloudflared container can use the same address, so no shared Docker network is needed.

Publish web on the server's LAN address (here `192.168.1.10`) with a `compose.override.yaml` next to `compose.yaml`:

```yaml
services:
  web:
    ports: !override ["192.168.1.10:8080:8080"]
```

Set the same address and port in `.env`, then apply:

```
LFT_LAN_HOST=192.168.1.10:8080
```

```bash
docker compose up -d
```

In the tunnel, route `files.example.com` to `http://192.168.1.10:8080`. On the LAN, open `http://192.168.1.10:8080`.

What you accept with this case:

- On the LAN the connection is plain HTTP: your session cookie and your files are not encrypted there. Without
  `LFT_LAN_HOST` the site works over HTTPS only.
- Every device on the LAN can use the app, and nothing asks for a login.
- A device that connects over the LAN address shows up in the access log with its LAN address. Visitors who come
  through the tunnel show up with the tunnel's address only.
- The address must match what the browser sends as `Host`, port included. If the server's LAN address changes,
  update both files.

## 3. Check the Host header

The app rejects any POST whose `Origin` does not match the `Host` header. Through the tunnel this works only if
cloudflared passes the public hostname through as `Host`, which is its default. Do not set `httpHostHeader` for this
route.

To check, from any machine:

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST -H 'Origin: https://files.example.com' -H 'X-LFT: 1' https://files.example.com/api/jobs
```

Any code other than `403` means the Host header arrives intact. `403` means it does not, and POST requests such as
starting a job fail with "Cross-site request rejected."

Then open `https://files.example.com` and run one small job.

Cloudflare features that add or rewrite scripts in pages can conflict with the page's Content Security Policy
(`default-src 'self'`). If the page works on the server but is blank or broken through the tunnel, check those
settings for this hostname first.

## 4. Close, open and logs

To close the service to everyone, and open it again (no restart needed):

```bash
docker compose exec web python -m app.cli close
```

```bash
docker compose exec web python -m app.cli open
```

The compose file limits the container logs to 3 files of 10 MB each. The web service runs with `--no-proxy-headers`,
so its access log shows the proxy's address, never a visitor's. If you run the image from your own compose file
(see [update-rollback.md](update-rollback.md)), add the same `logging` options and `--no-proxy-headers` there.

## Commands not run while writing this

No tunnel was available, so the `cloudflared` and `systemctl` commands and the dashboard steps were not run, and the
Host header check was run against `http://127.0.0.1:8080` rather than through a tunnel. The other commands were
run against this repository's `compose.yaml` for an earlier version; for 1.2.0 the install, session check, `close`
and `open` commands were run again.
