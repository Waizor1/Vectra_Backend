"""Expand error_reports for triage observability.

Adds the full set of context columns needed to actually debug a user-side
error without guessing:

- Release: app_version, commit_sha, bundle_hash
- Session/platform: session_id, platform, tg_platform, tg_version, viewport,
  dpr, connection_type, locale
- Runtime: page_age_ms, document_ready_state, document_visibility_state,
  online, save_data, hardware_concurrency, device_memory, js_heap_used_mb,
  js_heap_total_mb, js_heap_limit_mb, sw_controller, referrer
- User trail: breadcrumbs (JSONB)
- Triage workflow: severity_hint, fingerprint, occurrences, first_seen_at,
  last_seen_at, request_id

A unique partial index on `fingerprint` lets the route UPSERT identical
errors into a single row (incrementing `occurrences` and refreshing
`last_seen_at`) instead of inserting hundreds of duplicates per release.

Idempotent so it is safe under SCHEMA_INIT_GENERATE_ONLY=true startup
bootstrap.
"""

from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "error_reports"
            ADD COLUMN IF NOT EXISTS "app_version" VARCHAR(64) NULL,
            ADD COLUMN IF NOT EXISTS "commit_sha" VARCHAR(64) NULL,
            ADD COLUMN IF NOT EXISTS "bundle_hash" VARCHAR(64) NULL,
            ADD COLUMN IF NOT EXISTS "session_id" VARCHAR(64) NULL,
            ADD COLUMN IF NOT EXISTS "platform" VARCHAR(32) NULL,
            ADD COLUMN IF NOT EXISTS "tg_platform" VARCHAR(32) NULL,
            ADD COLUMN IF NOT EXISTS "tg_version" VARCHAR(16) NULL,
            ADD COLUMN IF NOT EXISTS "viewport_w" INT NULL,
            ADD COLUMN IF NOT EXISTS "viewport_h" INT NULL,
            ADD COLUMN IF NOT EXISTS "dpr" REAL NULL,
            ADD COLUMN IF NOT EXISTS "connection_type" VARCHAR(16) NULL,
            ADD COLUMN IF NOT EXISTS "locale" VARCHAR(16) NULL,
            ADD COLUMN IF NOT EXISTS "breadcrumbs" JSONB NULL,
            ADD COLUMN IF NOT EXISTS "severity_hint" VARCHAR(16) NULL,
            ADD COLUMN IF NOT EXISTS "request_id" VARCHAR(64) NULL,
            ADD COLUMN IF NOT EXISTS "fingerprint" VARCHAR(64) NULL,
            ADD COLUMN IF NOT EXISTS "occurrences" INT NOT NULL DEFAULT 1,
            ADD COLUMN IF NOT EXISTS "first_seen_at" TIMESTAMPTZ NULL,
            ADD COLUMN IF NOT EXISTS "last_seen_at" TIMESTAMPTZ NULL,
            ADD COLUMN IF NOT EXISTS "page_age_ms" INT NULL,
            ADD COLUMN IF NOT EXISTS "document_ready_state" VARCHAR(16) NULL,
            ADD COLUMN IF NOT EXISTS "document_visibility_state" VARCHAR(16) NULL,
            ADD COLUMN IF NOT EXISTS "online" BOOLEAN NULL,
            ADD COLUMN IF NOT EXISTS "save_data" BOOLEAN NULL,
            ADD COLUMN IF NOT EXISTS "hardware_concurrency" INT NULL,
            ADD COLUMN IF NOT EXISTS "device_memory" REAL NULL,
            ADD COLUMN IF NOT EXISTS "js_heap_used_mb" REAL NULL,
            ADD COLUMN IF NOT EXISTS "js_heap_total_mb" REAL NULL,
            ADD COLUMN IF NOT EXISTS "js_heap_limit_mb" REAL NULL,
            ADD COLUMN IF NOT EXISTS "sw_controller" VARCHAR(256) NULL,
            ADD COLUMN IF NOT EXISTS "referrer" VARCHAR(1024) NULL;
        CREATE UNIQUE INDEX IF NOT EXISTS "ux_error_reports_fingerprint"
            ON "error_reports" ("fingerprint")
            WHERE "fingerprint" IS NOT NULL;
        CREATE INDEX IF NOT EXISTS "ix_error_reports_last_seen_at"
            ON "error_reports" ("last_seen_at");
        CREATE INDEX IF NOT EXISTS "ix_error_reports_severity_status"
            ON "error_reports" ("triage_severity", "triage_status");
        CREATE INDEX IF NOT EXISTS "ix_error_reports_app_version"
            ON "error_reports" ("app_version");
        CREATE INDEX IF NOT EXISTS "ix_error_reports_platform"
            ON "error_reports" ("platform");
        CREATE INDEX IF NOT EXISTS "ix_error_reports_session_id"
            ON "error_reports" ("session_id")
            WHERE "session_id" IS NOT NULL;
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP INDEX IF EXISTS "ix_error_reports_session_id";
        DROP INDEX IF EXISTS "ix_error_reports_platform";
        DROP INDEX IF EXISTS "ix_error_reports_app_version";
        DROP INDEX IF EXISTS "ix_error_reports_severity_status";
        DROP INDEX IF EXISTS "ix_error_reports_last_seen_at";
        DROP INDEX IF EXISTS "ux_error_reports_fingerprint";
        ALTER TABLE "error_reports"
            DROP COLUMN IF EXISTS "referrer",
            DROP COLUMN IF EXISTS "sw_controller",
            DROP COLUMN IF EXISTS "js_heap_limit_mb",
            DROP COLUMN IF EXISTS "js_heap_total_mb",
            DROP COLUMN IF EXISTS "js_heap_used_mb",
            DROP COLUMN IF EXISTS "device_memory",
            DROP COLUMN IF EXISTS "hardware_concurrency",
            DROP COLUMN IF EXISTS "save_data",
            DROP COLUMN IF EXISTS "online",
            DROP COLUMN IF EXISTS "document_visibility_state",
            DROP COLUMN IF EXISTS "document_ready_state",
            DROP COLUMN IF EXISTS "page_age_ms",
            DROP COLUMN IF EXISTS "last_seen_at",
            DROP COLUMN IF EXISTS "first_seen_at",
            DROP COLUMN IF EXISTS "occurrences",
            DROP COLUMN IF EXISTS "fingerprint",
            DROP COLUMN IF EXISTS "request_id",
            DROP COLUMN IF EXISTS "severity_hint",
            DROP COLUMN IF EXISTS "breadcrumbs",
            DROP COLUMN IF EXISTS "locale",
            DROP COLUMN IF EXISTS "connection_type",
            DROP COLUMN IF EXISTS "dpr",
            DROP COLUMN IF EXISTS "viewport_h",
            DROP COLUMN IF EXISTS "viewport_w",
            DROP COLUMN IF EXISTS "tg_version",
            DROP COLUMN IF EXISTS "tg_platform",
            DROP COLUMN IF EXISTS "platform",
            DROP COLUMN IF EXISTS "session_id",
            DROP COLUMN IF EXISTS "bundle_hash",
            DROP COLUMN IF EXISTS "commit_sha",
            DROP COLUMN IF EXISTS "app_version";
    """
