# Vectra Backend

Backend for the Telegram bot, the public API, FastAdmin, background tasks, and Directus-backed operational tooling.

## Requirements

- Python 3.12
- Docker with `docker compose`
- Optional: Node/npm for Directus extension builds

Recommended workspace bootstrap from repository root:

```bash
./scripts/bootstrap-mac.sh
```

Manual bootstrap from `Vectra_Backend/`:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install pytest pytest-asyncio
cp .env.example .env
```

## Docker-first local setup

1. Prepare environment:

```bash
cp .env.example .env
```

2. Fill the required variables in `.env`:

- `POSTGRES_PASSWORD`
- `TELEGRAM_TOKEN`
- `TELEGRAM_WEBHOOK_SECRET`
- `TELEGRAM_MINIAPP_URL`
- `TELEGRAM_WEBAPP_URL`
- `REMNAWAVE_URL`
- `REMNAWAVE_TOKEN`
- `SCRIPT_API_URL`
- `ADMIN_TELEGRAM_ID`
- `ADMIN_LOGIN`
- `ADMIN_PASSWORD`
- `AUTH_JWT_SECRET`
- `DIRECTUS_KEY`
- `DIRECTUS_SECRET`
- `DIRECTUS_ADMIN_EMAIL`
- `DIRECTUS_ADMIN_PASSWORD`
- `PROMO_HMAC_SECRET`

3. Start infrastructure:

```bash
docker compose up -d bloobcat_db directus
```

4. Run the backend locally:

```bash
PYTHONPATH="$PWD" .venv/bin/python -m bloobcat
```

Health endpoint:

```text
http://localhost:33083/health
```

## Full Docker stack

```bash
docker compose up -d --build
```

- API: `http://localhost:33083`
- Directus: `http://localhost:8055`

## Validation

```bash
.venv/bin/python -m pytest tests -q
.venv/bin/python -m compileall bloobcat
docker compose config
```

From repository root, the equivalent commands are:

```bash
PYTHONPATH=Vectra_Backend Vectra_Backend/.venv/bin/python -m pytest Vectra_Backend/tests -q
Vectra_Backend/.venv/bin/python -m compileall Vectra_Backend
docker compose -f Vectra_Backend/docker-compose.yml config
```

## Directus extensions

Build-capable extensions live in `directus/extensions/**` and are included in the root macOS bootstrap/predeploy scripts.

Manual per-extension build example:

```bash
cd directus/extensions/tvpn-home
npm ci
npm run build
```

Hook-only packages can be syntax-smoked without a build step:

```bash
node --check directus/extensions/remnawave-sync/src/index.js
node --check directus/extensions/remnawave-sync/dist/index.js
```
