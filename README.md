# CastCharm

A self-hosted podcast manager with a clean web UI. Subscribe to RSS feeds, auto-download episodes, track playback, and manage your library — all from a single Docker container.

**[castcharm.org](https://www.castcharm.org)** · [Installation guide](https://www.castcharm.org/install.html) · [Android app](https://www.castcharm.org/android.html)

[![Version](https://img.shields.io/github/v/tag/CastCharm/castcharm?label=version&cacheSeconds=3600)](https://github.com/CastCharm/castcharm/tags)
![License](https://img.shields.io/github/license/CastCharm/castcharm?cacheSeconds=3600)
![Docker Image](https://img.shields.io/github/actions/workflow/status/CastCharm/castcharm/docker.yml?label=docker%20build)
[![Issues](https://img.shields.io/github/issues/CastCharm/castcharm)](https://github.com/CastCharm/castcharm/issues)
---

## Features

- **Feed management** — subscribe via RSS URL or add offline/manual feeds
- **Auto-download** — automatically download new episodes, with per-feed overrides and a keep-latest-N cleanup option
- **Playback tracking** — remembers position, marks episodes played, backlog stats
- **ID3 tagging** — write metadata to MP3 files with configurable field mappings
- **Clean RSS** — generates clean RSS feeds for use with podcast apps
- **Search** — full-text search across all episodes
- **Stats** — library-wide and per-feed statistics with charts
- **Themes** — 20+ built-in colour themes
- **Auth** — optional password protection
- **API** — full REST API with Swagger docs at `/api/docs`, usable by external clients via API keys

---

## Quick Start

```bash
# 1. Download the compose file
curl -O https://raw.githubusercontent.com/CastCharm/castcharm/main/docker-compose.yml

# 2. (Optional) configure paths and port
cp .env.example .env
$EDITOR .env

# 3. Start
docker compose up -d
```

Open **http://localhost:8000** — the setup wizard will guide you through initial configuration.

---

## Configuration

All configuration is done via environment variables (or a `.env` file next to `docker-compose.yml`). Most people only need these three:

| Variable | Default | Description |
|---|---|---|
| `PORT` | `8000` | Host port to expose |
| `DATA_PATH` | `./data` | Host path for the SQLite database and app state |
| `DOWNLOAD_PATH` | `./downloads` | Host path for downloaded audio files |

The `DATABASE_URL`, `DEFAULT_DOWNLOAD_PATH`, and `CLEAN_RSS_PATH` variables inside the container are set automatically by `docker-compose.yml` and don't normally need to be changed.

### Advanced options

Skip this unless you're running behind a reverse proxy in another container or building your own image.

| Variable | Default | Description |
|---|---|---|
| `CASTCHARM_TRUSTED_PROXIES` | *(empty)* | Comma-separated IPs allowed to set `X-Forwarded-For`. `127.0.0.1` and `::1` are trusted by default, so a proxy on the same host needs no change. Add your proxy's container IP if it runs separately, otherwise the login rate-limit lumps every failed attempt together under the proxy's address. |
| `APP_VERSION` | `dev` | Version string reported by `/api/status` and shown in the API docs. Set automatically by official container images. |

The login cookie's `Secure` flag is set automatically based on whether the current request came in over HTTPS — no configuration needed for either plain-HTTP or HTTPS deployments.

### Running behind a reverse proxy

CastCharm works behind nginx, Caddy, Traefik, or any other reverse proxy. If your proxy runs on the same host as CastCharm (the usual setup), no configuration is needed.

If your proxy runs in a *separate container* and you want the login rate-limit to see the real client IP instead of the proxy's, set `CASTCHARM_TRUSTED_PROXIES` to the proxy's container IP.

---

## Docker

### Pre-built image (GitHub Container Registry)

```yaml
services:
  castcharm:
    image: ghcr.io/castcharm/castcharm:latest
    container_name: castcharm
    ports:
      - "${PORT:-8000}:8000"
    volumes:
      - ${DATA_PATH:-./data}:/data
      - ${DOWNLOAD_PATH:-./downloads}:/downloads
    environment:
      - DATABASE_URL=sqlite:////data/castcharm.db
      - DEFAULT_DOWNLOAD_PATH=/downloads
      - CLEAN_RSS_PATH=/downloads/clean-rss
    restart: unless-stopped
```

### Build from source

```bash
git clone https://github.com/CastCharm/castcharm
cd castcharm
docker compose up -d --build
```

---

## API

Everything the web interface does goes through a REST API, and external clients
can use the same endpoints. Interactive reference: **`/api/docs`**.

### Enabling access

External API access is **on by default** — you're asked about it during the setup
wizard, and it can be changed any time under **Settings → External API**. On its
own it grants nothing: a client also needs a key.

To create one, press **Generate new key**, give it a name (e.g. `Android phone`),
and copy the key — it's shown once and never again.

Give each device or script its own key so you can revoke one without disturbing
the rest. The list shows when each key was last used, which is a quick way to tell
whether a client is actually connecting.

Turning the toggle **off** is a kill switch: every key stops working immediately,
including ones held by the mobile app. Keys aren't deleted, so switching it back
on restores all of them.

### Using a key

Send it as either header:

```bash
curl -H "Authorization: Bearer cc_your_key_here" http://localhost:8000/api/feeds
curl -H "X-API-Key: cc_your_key_here"            http://localhost:8000/api/status
```

### Native clients

Apps can enrol themselves instead of asking you to paste a key by hand. After a
normal username/password login, a client may call:

```
POST /api/auth/exchange-key    {"name": "Pixel 9"}
```

which trades the login session for a permanent key. The device then shows up in
the key list like any other client, and revoking it there cuts that device off.
This requires a real login session — a caller holding only an API key cannot
exchange for another one.

### Notes

- **A key has the same power as logging in** — it can read your library, change
  settings, and delete downloaded files. There are no read-only keys.
- **Keys can't manage keys.** Creating and revoking keys requires a browser
  session, so a leaked key can't mint replacements or hide its own revocation.
- **Turning off the toggle disables every key at once**, without deleting them.
- **If login is disabled, the whole instance is already open** to anyone who can
  reach it, with or without a key. Enable login under Settings → Security if you
  want keys to actually restrict anything.
- Browser-based apps on other origins aren't supported — there's no CORS layer.
  Scripts, native mobile apps, and anything server-side work fine.

---

## Data & Backups

- **Database**: `DATA_PATH/castcharm.db` — copy this file to back up all feeds, episodes, settings, and playback history.
- **Downloads**: `DOWNLOAD_PATH/` — your audio files, organised as `Podcast Name/YYYY/filename.mp3`.

To restore: stop the container, replace the files, start again.

---

## Development

```bash
# Install dependencies
pip install -r requirements.txt

# Run locally (SQLite in ./data, downloads in ./downloads)
DATABASE_URL=sqlite:///./data/castcharm.db \
DEFAULT_DOWNLOAD_PATH=./downloads \
uvicorn app.main:app --reload --port 8000
```

The frontend is plain HTML/CSS/JS — no build step required.

---

## License

MIT — see [LICENSE](LICENSE).

## AI Disclosure

Generative AI is being used for some aspects of development. All generated code is human-reviewed, human-modified where improvements can be made, and tested by humans.
