# Reaching the app from your phone

The backend binds `127.0.0.1:8000`. It is deliberately not reachable from the
internet: this machine has a public IP, and auth is opt-in — with no
`API_TOKEN` set, anyone who found the port could read every job, upload a
2 GB file, or call `POST /api/shutdown`.

Two ways to reach it. Start with Tailscale.

## Tailscale (recommended)

Your VM, your iPhone and your Windows PC are already on one tailnet, so there is
nothing to expose and no certificate to manage.

**One-time, in the admin console:** enable HTTPS certificates for the tailnet.
`tailscale serve` cannot issue a certificate without it.
<https://login.tailscale.com/admin/dns> → *HTTPS Certificates* → Enable.

**On the VM:**

```bash
tailscale serve --bg --https=443 http://127.0.0.1:8000
```

That is persistent: it survives reboots and you do not run it again. Check it
with `tailscale serve status`, and undo it with `tailscale serve --https=443 off`.

The app is then at `https://<machine>.<tailnet>.ts.net/` from any device signed
into the tailnet. Find the exact name with `tailscale status --json | jq -r .Self.DNSName`.

On the iPhone, open that URL in Safari and use **Share → Add to Home Screen**.
It installs as a standalone app: no browser chrome, its own icon, and the token
stays signed in.

### Why not Funnel

`tailscale funnel` publishes the same thing to the open internet. Serve keeps it
inside the tailnet and adds identity headers. Auth is opt-in, and the tailnet
setup above leaves it off by default. **Set `API_TOKEN` in `.env` before using
Funnel** -- once the app is public, the token becomes the only thing standing
between your jobs and the internet.

## Your own domain, later

When you want a real domain, put Caddy in front. It terminates TLS and rate
limits; the API token still does the authentication.

```bash
docker compose --profile domain up -d
```

`deploy/Caddyfile` holds the configuration. You need the domain's A record
pointed at this machine and ports 80 and 443 open in the Oracle Cloud security
list *and* in the instance firewall (`iptables`/`firewalld` — Oracle images
block everything but SSH by default, which is the step most people miss).

## The API token

Auth is opt-in. With no `API_TOKEN` set the API is open -- fine on
a private tailnet, since only devices on your tailnet can reach it at all. Put
a long random string in `.env` to require it on every request:

```bash
API_TOKEN=...
```

Every `/api` route requires it once set, except `/api/health`, which reports
only booleans and counts so a healthcheck or a proxy can use it without a
credential.

Use it from a script with either header:

```bash
curl -H "Authorization: Bearer $API_TOKEN" https://<host>/api/jobs
curl -H "X-API-Key: $API_TOKEN" https://<host>/api/jobs
```

## Checking it worked

```bash
curl -s http://127.0.0.1:8000/api/health            # 200, no token needed
```

With `API_TOKEN` set in `.env`:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/api/jobs   # 401
curl -s -o /dev/null -w '%{http_code}\n' -H "Authorization: Bearer $API_TOKEN" \
     http://127.0.0.1:8000/api/jobs                 # 200
```
