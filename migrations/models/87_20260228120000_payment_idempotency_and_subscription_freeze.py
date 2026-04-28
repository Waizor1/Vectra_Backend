from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "processed_payments"
            ADD COLUMN IF NOT EXISTS "processing_state" VARCHAR(32) NOT NULL DEFAULT 'idle';

        ALTER TABLE "processed_payments"
            ADD COLUMN IF NOT EXISTS "effect_applied" BOOL NOT NULL DEFAULT FALSE;

        ALTER TABLE "processed_payments"
            ADD COLUMN IF NOT EXISTS "attempt_count" INT NOT NULL DEFAULT 0;

        ALTER TABLE "processed_payments"
            ADD COLUMN IF NOT EXISTS "last_attempt_at" TIMESTAMPTZ;

        ALTER TABLE "processed_payments"
            ADD COLUMN IF NOT EXISTS "last_source" VARCHAR(32);

        ALTER TABLE "processed_payments"
            ADD COLUMN IF NOT EXISTS "last_error" TEXT;

        CREATE INDEX IF NOT EXISTS "idx_processed_payments_state"
            ON "processed_payments" ("processing_state");

        CREATE INDEX IF NOT EXISTS "idx_processed_payments_effect_applied"
            ON "processed_payments" ("effect_applied");

        UPDATE "processed_payments"
        SET "processing_state" = CASE
            WHEN "status" = 'pending' THEN 'pending'
            WHEN "status" = 'succeeded' THEN 'applied'
            WHEN "status" = 'canceled' THEN 'canceled'
            WHEN "status" = 'refunded' THEN 'refunded'
            ELSE 'idle'
        END,
        "effect_applied" = CASE WHEN "status" = 'succeeded' THEN TRUE ELSE FALSE END
        WHERE "processing_state" = 'idle';

        CREATE TABLE IF NOT EXISTS "subscription_freezes" (
            "id" SERIAL NOT NULL PRIMARY KEY,
            "user_id" BIGINT NOT NULL,
            "freeze_reason" VARCHAR(32) NOT NULL DEFAULT 'family_overlay',
            "is_active" BOOL NOT NULL DEFAULT TRUE,
            "base_remaining_days" INT NOT NULL DEFAULT 0,
            "base_expires_at_snapshot" DATE,
            "family_expires_at" DATE NOT NULL,
            "base_tariff_name" VARCHAR(255),
            "base_tariff_months" INT,
            "base_tariff_price" INT,
            "base_hwid_limit" INT,
            "base_lte_gb_total" INT,
            "base_lte_price_per_gb" DOUBLE PRECISION,
            "base_progressive_multiplier" DOUBLE PRECISION,
            "base_residual_day_fraction" DOUBLE PRECISION,
            "resume_applied" BOOL NOT NULL DEFAULT FALSE,
            "resumed_at" TIMESTAMPTZ,
            "resume_attempt_count" INT NOT NULL DEFAULT 0,
            "last_resume_error" TEXT,
            "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "updated_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS "idx_subscription_freezes_user_id"
            ON "subscription_freezes" ("user_id");

        CREATE INDEX IF NOT EXISTS "idx_subscription_freezes_is_active"
            ON "subscription_freezes" ("is_active");
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "subscription_freezes";

        DROP INDEX IF EXISTS "idx_processed_payments_state";
        DROP INDEX IF EXISTS "idx_processed_payments_effect_applied";

        ALTER TABLE "processed_payments" DROP COLUMN IF EXISTS "processing_state";
        ALTER TABLE "processed_payments" DROP COLUMN IF EXISTS "effect_applied";
        ALTER TABLE "processed_payments" DROP COLUMN IF EXISTS "attempt_count";
        ALTER TABLE "processed_payments" DROP COLUMN IF EXISTS "last_attempt_at";
        ALTER TABLE "processed_payments" DROP COLUMN IF EXISTS "last_source";
        ALTER TABLE "processed_payments" DROP COLUMN IF EXISTS "last_error";
    """
