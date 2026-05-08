# Deploy pipeline roadmap (post-2026-05-08 outage)

Background: on 2026-05-08 a 21-minute API outage was caused by a missing
compat-patch entry for the new `users.partner_link_mode` column. Recovery
was slow because the deploy pipeline (a) re-ran tests already covered by
CI, (b) rebuilt the Docker image on the prod server, and (c) had no
automatic rollback path.

This document tracks what shipped (P0 + P1) on the `improve/deploy-pipeline`
branch and what is still planned (P2). The phasing is deliberate: P0/P1 are
local, surgical, and reversible; P2 changes the deploy topology and needs a
separate maintenance window.

## P0 — landed (must-haves to stop today's class of regression)

| ID  | Change | File(s) |
| --- | --- | --- |
| P0.1 | New CI test asserting every Users column added in migrations ≥ 100 also lives in `_apply_generate_schema_compat_patches` | `tests/test_compat_patches_complete.py`, `bloobcat/__main__.py` |
| P0.2 | `Auto Deploy Backend` now waits for `Backend CI` to succeed (`workflow_run` trigger). Direct `push:` trigger removed; deploys run against the exact SHA that passed CI (`ref: workflow_run.head_sha`). | `.github/workflows/auto-deploy.yml` |
| P0.3 | Removed the duplicate pytest run from the deploy job (saves ~3-5 min per deploy; CI now gates this) | `.github/workflows/auto-deploy.yml` |
| P0.4 | Auto-rollback on `wait_bloobcat_health` failure: capture the previous image's SHA + tag *before* `up --build`, retag and recreate on health failure. Manual intervention only required when there's no previous image OR the rollback also fails its health check. | `.github/workflows/auto-deploy.yml` |

## P1 — landed (speed)

| ID  | Change | File(s) |
| --- | --- | --- |
| P1.1 | pip cache enabled in `Backend CI` via `actions/setup-python@v6 cache: 'pip'` (saves ~60 s per CI run) | `.github/workflows/ci.yml` |
| P1.2 | Conditional Directus super-setup: skipped when `directus/` and `scripts/directus_*.py` haven't changed since the last successful run. State persists in `$PROJECT_PATH/.deploy-state/last_super_setup_sha` (excluded from rsync `--delete`). | `.github/workflows/auto-deploy.yml` |
| P1.3 | `build-and-push-image` job in `Backend CI` builds the `bloobcat` image on every successful main commit and pushes to `ghcr.io/<owner>/vectra-backend` with tags `:main`, `:sha-<short>`, `:latest`. Buildx cache via `type=gha`. | `.github/workflows/ci.yml`, new `docker-compose.ghcr.yml` |

The deploy script understands the GHCR image but **only consumes it when
`vars.USE_GHCR_PREBUILT_IMAGE` is set to `'true'`**. Until that flag is
flipped, deploys keep rebuilding on the prod server (existing behaviour).
The flag is opt-in so the GHCR pipeline can be observed for at least one CI
cycle before wiring it into prod.

### Flipping to GHCR prebuilt images (when ready)

1. Wait until at least one merge to `main` after this PR has produced a
   working `ghcr.io/<owner>/vectra-backend:main` image (visible in
   GitHub → Packages).
2. In the `Vectra_Backend` repo, navigate to
   `Settings → Variables → Actions` and add:
   - `USE_GHCR_PREBUILT_IMAGE = true`
   - (optional) `VECTRA_BACKEND_IMAGE_TAG = main` — for commit-pinned deploys
     set this to `sha-<short>` instead.
3. The next deploy will:
   - Login to `ghcr.io` with the workflow's short-lived `GITHUB_TOKEN`.
   - Layer `docker-compose.ghcr.yml` on top of the existing compose chain.
   - Run `docker compose pull bloobcat` (registry pull, ~10-30 s).
   - Run `docker compose up -d --no-build bloobcat directus` (no rebuild).
4. If `pull` fails 3× the deploy automatically falls back to the local
   `--build` path so the failure surface stays small.

Expected savings once enabled: ~60-150 s per deploy (replaces the
60-180 s `compose build` step).

## P2 — planned (architectural; needs a separate window)

These are larger and worth shipping deliberately, not tonight.

### P2.1 — Pre-app migration step

**Problem.** The current `_apply_generate_schema_compat_patches` block is a
human-maintained list of `ALTER TABLE` statements that must mirror the
model. The CI guard (P0.1) catches the most common drift, but the helper
itself is still a workaround for the architectural fact that lifespan
queries the DB before the migration step has run.

**Proposal.** Add a one-shot `bloobcat_migrate` service to docker-compose
whose only job is `python scripts/apply_migrations.py`. The main
`bloobcat` service depends on `bloobcat_migrate` with
`condition: service_completed_successfully`. Migrations run before the
app starts, so the schema is always current when lifespan kicks off.
The compat-patches block can then be **deleted entirely** (a class of bug
goes away).

**Effort.** ~1 day. Touches docker-compose.yml, the deploy workflow, and
the `_initialize_schema_without_aerich` path in `bloobcat/__main__.py`.
Needs a careful staged rollout: keep both paths for one merge cycle, flip
default, then delete compat-patches.

### P2.2 — Staging environment

**Problem.** Every PR-to-main merge goes straight to prod. There is no
intermediate target to catch issues that only appear with prod-shaped
data, traffic, or env vars.

**Proposal.** Ports 33084/8056 on the same host run a `staging` compose
project pointing at a daily-restored prod-DB snapshot
(`pg_dump | pg_restore`). The deploy workflow is split:
`auto-deploy-staging.yml` triggered on every Backend CI success, and
`auto-deploy-prod.yml` triggered manually (`workflow_dispatch`) or by a
`production` GitHub Environment with required reviewer.

Needs DNS allocation (`staging-api.vectra-pro.net`), Caddy config, and a
secrets duplicate. Staging-specific overrides in `docker-compose.staging.yml`.

**Effort.** ~1 day plus DNS turnaround.

### P2.3 — Blue/Green via two compose projects

**Problem.** `docker compose up --build` swaps the live container in
place; there is no overlap window where both versions can serve traffic.
Rollback (P0.4) is tag-and-recreate, which still has a brief downtime
while compose stops the old container and starts the new one from the
old image.

**Proposal.** Two compose projects: `bloobcat-blue` and `bloobcat-green`,
each binding to different internal ports. A Caddy `upstream` directive
points at the active colour. Deploy:

1. Build new image into the **inactive** colour.
2. Wait for its `/health` to come up.
3. Run smoke tests against the inactive port.
4. Atomically reload Caddy to switch upstream.
5. Old colour stays around until next deploy.

Rollback is reload-Caddy-only (~1 second).

**Effort.** ~1-2 days. Needs Caddy config templating (it's currently
managed by the Frontend deploy workflow in `Ensure frontend Caddy static
rules`; backend lives behind separate Caddy or nginx). Worth doing only
after P2.1 lands so migrations are clean.

### P2.4 — Observability: per-step deploy timing report

**Problem.** Today nothing tells the operator which step ate the deploy
budget. Speeding up a step blindly is hard without measurement.

**Proposal.** Wrap each deploy step with `time` capture, write to
`$DEPLOY_LOG_FILE`, post a digest as the final line of the workflow log
and (optional) to a Slack/Telegram channel. Cheap; ~half-day.

## What's intentionally NOT in this branch

- **Pinning Dockerfile dependencies** — tangential to deploy speed.
- **Switching off SCHEMA_INIT_GENERATE_ONLY=true on prod** — that's the
  P2.1 outcome, not a separate flip.
- **Compose v2 → swarm/k8s migration** — out of scope for this iteration.

## Verification log (local, this branch)

- `actionlint .github/workflows/auto-deploy.yml .github/workflows/ci.yml` — only the pre-existing SC2029 informational note on line 78.
- `bash -n` on the extracted remote heredoc — clean.
- `docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.ghcr.yml config --quiet` — clean.
- `pytest tests/test_compat_patches_complete.py tests/test_partner_link_mode.py tests/test_partner_qr_creation.py tests/test_runtime_state_verification.py` — 27 passed.
- Full `pytest -q` — see corresponding entry in commit message.
