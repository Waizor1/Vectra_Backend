"""Create golden_period_configs / golden_periods / golden_period_payouts tables.

Brand-new tables, all empty on first deploy, so the CREATE TABLE statements are
safe inside the migration transaction. Indexes that need CONCURRENTLY (the
post-107 policy enforced by `scripts/check_migration_safety.py`) ship via
`_apply_concurrent_index_patches` in `bloobcat/__main__.py` instead — that
helper runs each `CREATE INDEX CONCURRENTLY ... IF NOT EXISTS` outside a
transaction so the lock window is short and the operation is idempotent.

The migration also seeds a single `golden_period_configs` row with
`slug='default'` and `is_enabled=false`. That row is the singleton that the
service layer reads on every request; ops can flip `is_enabled=true` from the
Directus admin extension when the FE banners are ready. Default is OFF so
deploying this migration to production has zero behavioral effect.
"""

from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "golden_period_configs" (
            "id" SERIAL PRIMARY KEY,
            "slug" VARCHAR(64) NOT NULL UNIQUE,
            "default_cap" INT NOT NULL DEFAULT 15,
            "payout_amount_rub" INT NOT NULL DEFAULT 100,
            "eligibility_min_active_days" INT NOT NULL DEFAULT 3,
            "window_hours" INT NOT NULL DEFAULT 24,
            "is_enabled" BOOLEAN NOT NULL DEFAULT FALSE,
            "clawback_window_days" INT NOT NULL DEFAULT 30,
            "message_templates" JSONB NOT NULL DEFAULT '{}'::jsonb,
            "signal_thresholds" JSONB NOT NULL DEFAULT '{}'::jsonb,
            "created_at" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            "updated_at" TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        INSERT INTO "golden_period_configs" ("slug", "is_enabled")
            VALUES ('default', FALSE)
            ON CONFLICT ("slug") DO NOTHING;

        CREATE TABLE IF NOT EXISTS "golden_periods" (
            "id" SERIAL PRIMARY KEY,
            "user_id" BIGINT NOT NULL REFERENCES "users" ("id") ON DELETE CASCADE,
            "config_id" INT REFERENCES "golden_period_configs" ("id") ON DELETE SET NULL,
            "started_at" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            "expires_at" TIMESTAMPTZ NOT NULL,
            "cap" INT NOT NULL,
            "payout_amount_rub" INT NOT NULL DEFAULT 100,
            "paid_out_count" INT NOT NULL DEFAULT 0,
            "total_paid_rub" INT NOT NULL DEFAULT 0,
            "status" VARCHAR(16) NOT NULL DEFAULT 'active',
            "seen_at" TIMESTAMPTZ,
            "triggered_by_active_days" INT NOT NULL,
            "created_at" TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS "golden_period_payouts" (
            "id" SERIAL PRIMARY KEY,
            "golden_period_id" INT NOT NULL REFERENCES "golden_periods" ("id") ON DELETE CASCADE,
            "referrer_user_id" BIGINT NOT NULL REFERENCES "users" ("id") ON DELETE CASCADE,
            "referred_user_id" BIGINT NOT NULL REFERENCES "users" ("id") ON DELETE CASCADE,
            "amount_rub" INT NOT NULL,
            "status" VARCHAR(16) NOT NULL DEFAULT 'optimistic',
            "paid_at" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            "confirmed_at" TIMESTAMPTZ,
            "clawed_back_at" TIMESTAMPTZ,
            "clawback_reason" VARCHAR(64),
            "clawback_payload" JSONB,
            "clawback_balance_rub" INT,
            "clawback_days_removed" INT,
            "clawback_lte_gb_removed" DECIMAL(10, 2),
            "created_at" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT "uq_golden_payout_period_referred"
                UNIQUE ("golden_period_id", "referred_user_id")
        );
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP INDEX IF EXISTS "uq_golden_periods_user_active";
        DROP INDEX IF EXISTS "ix_golden_periods_user";
        DROP INDEX IF EXISTS "ix_golden_periods_status_expires";
        DROP INDEX IF EXISTS "ix_golden_periods_started_at";
        DROP INDEX IF EXISTS "ix_golden_payouts_referrer_status";
        DROP INDEX IF EXISTS "ix_golden_payouts_status_paid_at";
        DROP TABLE IF EXISTS "golden_period_payouts";
        DROP TABLE IF EXISTS "golden_periods";
        DROP TABLE IF EXISTS "golden_period_configs";
    """
