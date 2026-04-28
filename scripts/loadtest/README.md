# Local loadtest harness (heavy simulation)

This folder provides a **local-only, test-tagged** load harness for heavy simulation.  
It does not modify application business logic and is safe to run repeatedly with explicit run ids.

## Files

- `prepare_loadtest_data.py` — creates/updates test-tagged users and writes token/id artifact JSON.
- `k6_battle.js` — k6 scenarios (warmup/heavy/cooldown) for read paths; delete canaries are opt-in.
- `cleanup_loadtest_data.py` — dry-run by default; deletes only harness-tagged users with `--apply`.

## Prerequisites

- Running backend (`bloobcat`) reachable at `http://127.0.0.1:33083` (or custom `BASE_URL`).
- Running Directus reachable at `http://127.0.0.1:8055` (or custom `DIRECTUS_URL`).
- Ensure the running `bloobcat` container has the latest script files before `docker compose exec`:

```bash
docker compose build bloobcat && docker compose up -d bloobcat
```

- Explicit mutation confirmation is required for writes/deletes:
  - `ALLOW_LOADTEST_MUTATIONS=true`

### Default mode (safe/read-only)

- `k6_battle.js` runs **read-only scenarios by default**.
- Delete canary scenarios are enabled only with explicit opt-in:
  - `ENABLE_MUTATION_CANARIES=true`
- In default read-only mode, `ADMIN_INTEGRATION_TOKEN`, `DIRECTUS_EMAIL`, and `DIRECTUS_PASSWORD` are **not required**.

## 1) Prepare test data (inside bloobcat runtime context)

Run from `Vectra_Backend/`:

```bash
docker compose exec -T bloobcat env ALLOW_LOADTEST_MUTATIONS=true \
  python scripts/loadtest/prepare_loadtest_data.py \
  --run-id wave-001 \
  --regular-count 500 \
  --partner-count 120 \
  --delete-api-count 120 \
  --delete-directus-count 120
```

> `run-id` is normalized in both prepare/cleanup: non `[a-zA-Z0-9_-]` chars become `-`, then truncated to 40 chars.
> `cleanup --run-id` matches current markers (`run_id=<normalized_run_id>`) in `username`/`utm`/`full_name`
> and keeps compatibility with legacy marker style.

Artifact output:

- `scripts/loadtest/artifacts/loadtest_data.json`

## 2) Run k6 battle via docker

### 2a) Default read-only run (recommended baseline)

Run from `Vectra_Backend/`:

```bash
docker run --rm \
  --network host \
  -v "${PWD}/scripts/loadtest:/work" \
  -w /work \
  -e BASE_URL="http://127.0.0.1:33083" \
  -e DIRECTUS_URL="http://127.0.0.1:8055" \
  -e LOADTEST_DATA_PATH="/work/artifacts/loadtest_data.json" \
  grafana/k6:latest run k6_battle.js \
  --summary-export /work/artifacts/k6_summary.json
```

### 2b) Mutation canary run (explicit opt-in)

Run from `Vectra_Backend/`:

```bash
docker run --rm \
  --network host \
  -v "${PWD}/scripts/loadtest:/work" \
  -w /work \
  -e BASE_URL="http://127.0.0.1:33083" \
  -e DIRECTUS_URL="http://127.0.0.1:8055" \
  -e LOADTEST_DATA_PATH="/work/artifacts/loadtest_data.json" \
  -e ENABLE_MUTATION_CANARIES=true \
  -e ADMIN_INTEGRATION_TOKEN="$ADMIN_INTEGRATION_TOKEN" \
  -e DIRECTUS_EMAIL="$DIRECTUS_EMAIL" \
  -e DIRECTUS_PASSWORD="$DIRECTUS_PASSWORD" \
  grafana/k6:latest run k6_battle.js \
  --summary-export /work/artifacts/k6_summary.json
```

### 2c) Full rerun gate (10+40+10) with temporary trust-proxy override + XFF simulation

This is a **temporary test-only runtime configuration** for rerun-readiness checks.

1) Start `bloobcat` with loadtest override (`RATE_LIMIT_TRUSTED_PROXIES` is injected only via override):

```bash
RATE_LIMIT_TRUSTED_PROXIES="198.18.0.0/15,127.0.0.1/32" \
docker compose -f docker-compose.yml -f docker-compose.loadtest.override.yml up -d bloobcat
```

2) Run full k6 battle with mutation canaries and XFF simulation enabled:

```bash
docker run --rm \
  --network host \
  -v "${PWD}/scripts/loadtest:/work" \
  -w /work \
  -e BASE_URL="http://127.0.0.1:33083" \
  -e DIRECTUS_URL="http://127.0.0.1:8055" \
  -e LOADTEST_DATA_PATH="/work/artifacts/loadtest_data.json" \
  -e ENABLE_MUTATION_CANARIES=true \
  -e ENABLE_XFF_SIMULATION=true \
  -e XFF_POOL_CIDR_BASE="198.18.0.0/15" \
  -e XFF_POOL_SIZE="4096" \
  -e ADMIN_INTEGRATION_TOKEN="$ADMIN_INTEGRATION_TOKEN" \
  -e DIRECTUS_EMAIL="$DIRECTUS_EMAIL" \
  -e DIRECTUS_PASSWORD="$DIRECTUS_PASSWORD" \
  grafana/k6:latest run k6_battle.js \
  --summary-export /work/artifacts/k6_summary.json
```

### Rollback (disable temporary trust-proxy override)

Restart `bloobcat` **without** the override file:

```bash
docker compose -f docker-compose.yml up -d bloobcat
```

Summary output file:

- `scripts/loadtest/artifacts/k6_summary.json`

## 3) Cleanup harness users

Dry-run (safe default):

```bash
docker compose exec -T bloobcat python scripts/loadtest/cleanup_loadtest_data.py --run-id wave-001
```

Apply deletion:

```bash
docker compose exec -T bloobcat env ALLOW_LOADTEST_MUTATIONS=true \
  python scripts/loadtest/cleanup_loadtest_data.py --run-id wave-001 --apply
```

Cleanup all harness-tagged runs:

```bash
docker compose exec -T bloobcat env ALLOW_LOADTEST_MUTATIONS=true \
  python scripts/loadtest/cleanup_loadtest_data.py --apply
```

## Caveats

- **Mixed external mode** (local backend + external services or vice versa) can distort latency/error profiles.
- Error budget includes **all 4xx + 5xx** responses (k6 `http_req_failed` threshold applies globally).
- Delete scenarios are intentionally low-rate canaries and may exhaust their candidate pools before test end.
- Delete scenarios run only when `ENABLE_MUTATION_CANARIES=true` is provided.
- For mutation canary runs (including full rerun 10+40+10), artifact delete pools must be non-empty:
  - `delete_api_user_ids` must contain at least 1 id.
  - `delete_directus_user_ids` must contain at least 1 id.
  If either pool is empty, `setup()` fails fast; regenerate artifact via
  `scripts/loadtest/prepare_loadtest_data.py` with non-zero `--delete-api-count` and
  `--delete-directus-count` for the same run.
- When mutation canaries are enabled, `k6_battle.js` validates delete ID pools against the deterministic
  harness range derived from `artifact.run_id` (`base_id = 8_000_000_000 + (crc32(run_id) % 900_000) * 1_000_000`).
  The run fails in `setup()` if any delete id is outside its expected pool:
  - `delete_api_user_ids`: `[base_id + 2*1_000_000, base_id + 3*1_000_000)`
  - `delete_directus_user_ids`: `[base_id + 3*1_000_000, base_id + 4*1_000_000)`
- Directus delete canary treats `200/204/404` as acceptable idempotent outcomes to avoid false failures.
- Directus delete canary now proactively refreshes/login for token before each delete iteration (plus keeps
  401 retry refresh), reducing token-expiry drift during long 10+40+10 runs.
- When `ENABLE_XFF_SIMULATION=true`, app requests include synthetic `X-Forwarded-For` values from a deterministic pool (`XFF_POOL_CIDR_BASE` + `XFF_POOL_SIZE`).
