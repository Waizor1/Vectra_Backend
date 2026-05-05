from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "users"
            ADD COLUMN IF NOT EXISTS "trial_started_at" TIMESTAMPTZ;

        UPDATE "users"
        SET "trial_started_at" = COALESCE("registration_date", "created_at", NOW())
        WHERE "trial_started_at" IS NULL
          AND "used_trial" = TRUE;

        CREATE TABLE IF NOT EXISTS "analytics_payment_events" (
            "id" SERIAL NOT NULL PRIMARY KEY,
            "payment_id" VARCHAR(128) NOT NULL UNIQUE,
            "user_id" BIGINT NOT NULL,
            "paid_at" TIMESTAMPTZ NOT NULL,
            "provider" VARCHAR(32),
            "kind" VARCHAR(32) NOT NULL DEFAULT 'subscription',
            "tariff_kind" VARCHAR(32),
            "months" INT,
            "device_count" INT,
            "subscription_revenue_rub" DECIMAL(12,2) NOT NULL DEFAULT 0,
            "lte_revenue_rub" DECIMAL(12,2) NOT NULL DEFAULT 0,
            "amount_external_rub" DECIMAL(12,2) NOT NULL DEFAULT 0,
            "amount_from_balance_rub" DECIMAL(12,2) NOT NULL DEFAULT 0,
            "lte_gb_purchased" DOUBLE PRECISION NOT NULL DEFAULT 0,
            "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "updated_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS "idx_analytics_payment_events_user_id"
            ON "analytics_payment_events" ("user_id");
        CREATE INDEX IF NOT EXISTS "idx_analytics_payment_events_paid_kind"
            ON "analytics_payment_events" ("paid_at", "kind");

        CREATE TABLE IF NOT EXISTS "analytics_service_daily" (
            "id" SERIAL NOT NULL PRIMARY KEY,
            "day" DATE NOT NULL,
            "product" VARCHAR(32) NOT NULL,
            "traffic_bytes" BIGINT NOT NULL DEFAULT 0,
            "traffic_gb" DOUBLE PRECISION NOT NULL DEFAULT 0,
            "subscription_revenue_rub" DECIMAL(12,2) NOT NULL DEFAULT 0,
            "lte_revenue_rub" DECIMAL(12,2) NOT NULL DEFAULT 0,
            "amount_external_rub" DECIMAL(12,2) NOT NULL DEFAULT 0,
            "amount_from_balance_rub" DECIMAL(12,2) NOT NULL DEFAULT 0,
            "lte_gb_purchased" DOUBLE PRECISION NOT NULL DEFAULT 0,
            "payments_count" INT NOT NULL DEFAULT 0,
            "paying_users" INT NOT NULL DEFAULT 0,
            "rub_per_gb" DOUBLE PRECISION NOT NULL DEFAULT 0,
            "last_calculated_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT "uid_analytics_service_daily_day_product" UNIQUE ("day", "product")
        );

        CREATE INDEX IF NOT EXISTS "idx_analytics_service_daily_day_product"
            ON "analytics_service_daily" ("day", "product");

        CREATE TABLE IF NOT EXISTS "analytics_trial_daily" (
            "id" SERIAL NOT NULL PRIMARY KEY,
            "day" DATE NOT NULL UNIQUE,
            "new_trials" INT NOT NULL DEFAULT 0,
            "active_trials" INT NOT NULL DEFAULT 0,
            "traffic_bytes" BIGINT NOT NULL DEFAULT 0,
            "traffic_gb" DOUBLE PRECISION NOT NULL DEFAULT 0,
            "top_user_id" BIGINT,
            "top_user_traffic_gb" DOUBLE PRECISION NOT NULL DEFAULT 0,
            "flagged_users_count" INT NOT NULL DEFAULT 0,
            "last_calculated_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS "analytics_trial_risk_flags" (
            "id" SERIAL NOT NULL PRIMARY KEY,
            "user_id" BIGINT NOT NULL,
            "day" DATE NOT NULL,
            "traffic_gb" DOUBLE PRECISION NOT NULL DEFAULT 0,
            "share_pct" DOUBLE PRECISION NOT NULL DEFAULT 0,
            "reason" VARCHAR(64) NOT NULL,
            "severity" VARCHAR(24) NOT NULL DEFAULT 'warning',
            "status" VARCHAR(24) NOT NULL DEFAULT 'new',
            "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "updated_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT "uid_analytics_trial_risk_user_day_reason" UNIQUE ("user_id", "day", "reason")
        );

        CREATE INDEX IF NOT EXISTS "idx_analytics_trial_risk_user_id"
            ON "analytics_trial_risk_flags" ("user_id");
        CREATE INDEX IF NOT EXISTS "idx_analytics_trial_risk_day"
            ON "analytics_trial_risk_flags" ("day");
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "analytics_trial_risk_flags";
        DROP TABLE IF EXISTS "analytics_trial_daily";
        DROP TABLE IF EXISTS "analytics_service_daily";
        DROP TABLE IF EXISTS "analytics_payment_events";

        ALTER TABLE "users" DROP COLUMN IF EXISTS "trial_started_at";
    """
