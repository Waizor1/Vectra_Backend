from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "referral_cashback_rewards" (
            "id" SERIAL PRIMARY KEY,
            "payment_id" VARCHAR(128) NOT NULL UNIQUE,
            "referrer_user_id" BIGINT NOT NULL REFERENCES "users" ("id") ON DELETE CASCADE,
            "referred_user_id" BIGINT NOT NULL REFERENCES "users" ("id") ON DELETE CASCADE,
            "amount_external_rub" INT NOT NULL DEFAULT 0,
            "cashback_percent" INT NOT NULL DEFAULT 0,
            "reward_rub" INT NOT NULL DEFAULT 0,
            "level_key" VARCHAR(32) NOT NULL,
            "created_at" TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE INDEX IF NOT EXISTS "ix_referral_cashback_payment"
            ON "referral_cashback_rewards" ("payment_id");
        CREATE INDEX IF NOT EXISTS "ix_referral_cashback_referrer"
            ON "referral_cashback_rewards" ("referrer_user_id");
        CREATE INDEX IF NOT EXISTS "ix_referral_cashback_referred"
            ON "referral_cashback_rewards" ("referred_user_id");
        CREATE INDEX IF NOT EXISTS "ix_referral_cashback_level"
            ON "referral_cashback_rewards" ("level_key");
        CREATE INDEX IF NOT EXISTS "ix_referral_cashback_created"
            ON "referral_cashback_rewards" ("created_at");

        CREATE TABLE IF NOT EXISTS "referral_level_rewards" (
            "id" SERIAL PRIMARY KEY,
            "user_id" BIGINT NOT NULL REFERENCES "users" ("id") ON DELETE CASCADE,
            "level_key" VARCHAR(32) NOT NULL,
            "status" VARCHAR(16) NOT NULL DEFAULT 'available',
            "reward_type" VARCHAR(32),
            "reward_value" INT,
            "opened_at" TIMESTAMPTZ,
            "created_at" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT "uid_referral_level_rewards_user_level" UNIQUE ("user_id", "level_key")
        );

        CREATE INDEX IF NOT EXISTS "ix_referral_level_rewards_user"
            ON "referral_level_rewards" ("user_id");
        CREATE INDEX IF NOT EXISTS "ix_referral_level_rewards_status"
            ON "referral_level_rewards" ("status");
        CREATE INDEX IF NOT EXISTS "ix_referral_level_rewards_level"
            ON "referral_level_rewards" ("level_key");
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "referral_level_rewards";
        DROP TABLE IF EXISTS "referral_cashback_rewards";
    """
