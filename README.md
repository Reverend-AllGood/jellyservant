# JellyServant

**JellyServant** is a self-hosted web UI that syncs your Jellyfin library to `.strm` + `.nfo` files, making your media available to secondary Jellyfin servers or any Kodi-compatible player — without copying actual media files.

> **Version 1.6** — Proxy-agnostic. Works with Nginx Proxy Manager, Traefik, Caddy, Cloudflare Tunnels, or plain HTTP on your LAN. No reverse proxy is required or bundled.

---

## Features

- Browse and selectively sync movies and TV shows from any Jellyfin server
- Generates `.strm` stream links and `.nfo` metadata + poster files
- Scheduled auto-sync (cron-style, per-day, configurable timezone)
- Auto-add new library items on scheduled runs
- Orphan detection — removes `.strm` files for deleted media
- Clean web UI — no JavaScript frameworks, no build step

---

## Quick Start

### Requirements

- Docker + Docker Compose
- A running Jellyfin server
- A reverse proxy **or** direct LAN access (see setup options below)

### 1. Clone the repo

```bash
git clone https://github.com/yourname/jellyservant.git
cd jellyservant
```

### 2. Choose your setup

---

## Setup Options

JellyServant exposes **port 5000** by default. How you access it from outside depends on what you already have running. Pick the option that matches your setup.

---

### Option A — Nginx Proxy Manager (recommended for existing NPM users)

No changes needed. Use the default compose file.

```bash
docker compose up -d
```

Then in NPM:
- **Domain:** `jellyservant.yourdomain.com`
- **Forward Hostname/IP:** your Docker host IP (or `jellyservant` if on the same Docker network)
- **Forward Port:** `5000`
- Enable **Websockets** and **Block Common Exploits** as desired
- Request an SSL certificate via the NPM UI

---

### Option B — Traefik

Add these labels to the `jellyservant` service in `docker-compose.yml`, and remove the `ports:` block:

```yaml
services:
  jellyservant:
    build: .
    expose:
      - "5000"
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.jellyservant.rule=Host(`jellyservant.yourdomain.com`)"
      - "traefik.http.routers.jellyservant.entrypoints=websecure"
      - "traefik.http.routers.jellyservant.tls.certresolver=myresolver"
      - "traefik.http.services.jellyservant.loadbalancer.server.port=5000"
    networks:
      - traefik_network   # must match your Traefik network name
```

---

### Option C — Caddy (no existing proxy)

If you don't have a reverse proxy, a minimal Caddy sidecar is included. Caddy handles HTTPS automatically with a free Let's Encrypt certificate.

```bash
# Set your domain and email (required for a real cert)
export DOMAIN=jellyservant.yourdomain.com
export ACME_EMAIL=you@example.com

docker compose -f docker-compose.https.yml up -d
```

Your domain's A record must point at this server before you run the above — Let's Encrypt needs to reach it to issue the certificate.

**LAN / local use (no domain):** just run the command without setting `DOMAIN`. Caddy will use a self-signed certificate. Your browser will show a warning — this is normal. Add a security exception to proceed.

---

### Option D — Cloudflare Tunnel (zero open ports)

If you use Cloudflare Tunnel, no port mapping or reverse proxy is needed at all.

1. Use the default `docker compose up -d`
2. In your Cloudflare Tunnel config, point a hostname at `http://localhost:5000`
3. Cloudflare handles HTTPS end-to-end

---

### Option E — LAN only (no HTTPS)

If JellyServant only needs to be reachable on your local network:

```bash
docker compose up -d
```

Access it at `http://<your-server-ip>:5000`. No proxy required.

---

## Configuration (in-app)

Open the **Config** tab in the UI and fill in:

| Field | Description |
|---|---|
| **Source Jellyfin URL** | Full URL of your Jellyfin server, e.g. `https://jellyfin.example.com` |
| **Bridge Domain** | The domain/IP that your secondary server will use to stream, e.g. `jellyfin.example.com` |
| **API Key** | Your Jellyfin API key (Dashboard → API Keys) |
| **Proxy Username** | Username for the `.strm` auth credential (can be a Jellyfin user or proxy user) |
| **Proxy Password** | Password for the above |

---

## Directory Structure

```
jellyservant/
├── app.py                     # Flask backend
├── Dockerfile
├── docker-compose.yml         # Default (HTTP, port 5000)
├── docker-compose.https.yml   # Optional Caddy sidecar (HTTPS, no existing proxy needed)
├── Caddyfile                  # Used by docker-compose.https.yml only
├── requirements.txt
├── templates/
│   └── index.html
├── config/                    # Auto-created — holds config.json (persisted via volume)
└── output/                    # Auto-created — holds .strm / .nfo / poster files
```

---

## Output Format

For each synced item JellyServant writes:

**Movies** → `output/Movies/Title (Year)/Title.strm` + `movie.nfo` + `poster.jpg`

**TV Shows** → `output/TV Shows/Show Name/Season XX/Show Name - SXXEXX.strm`

The `.strm` files contain a direct stream URL with credentials embedded:
```
https://user:pass@yourdomain.com/Videos/<ItemId>/stream?static=true
```

---

## Volumes

| Mount | Purpose |
|---|---|
| `./config:/config` | Config file, schedules, sync log, known IDs |
| `./output:/output` | Generated `.strm`, `.nfo`, and poster files |

When using `docker-compose.https.yml`, a `caddy_data` Docker volume is also created to persist TLS certificates across container rebuilds. **Do not delete this volume** or Caddy will re-request certificates on every startup (Let's Encrypt rate limits apply).

---

## Upgrading

```bash
git pull
docker compose up -d --build
```

Config and output files are preserved in their mounted volumes.

---

## Changelog

### 1.6
- Removed NPM dependency — now fully proxy-agnostic
- Added `docker-compose.https.yml` with optional Caddy sidecar for users without an existing proxy
- Renamed "Nginx Bridge" config labels to generic "Proxy Username / Password"
- Added `/api/version` endpoint
- Added setup documentation for NPM, Traefik, Caddy, Cloudflare Tunnel, and LAN-only

### 1.5
- Initial public beta
- Library browser with selective sync
- Scheduled auto-sync with cron triggers
- Auto-add new items, orphan removal
- Sync log

---

## License

MIT
