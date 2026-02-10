from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "referral_rewards" (
            "id" SERIAL PRIMARY KEY,
            "referred_user_id" BIGINT NOT NULL REFERENCES "users" ("id") ON DELETE CASCADE,
            "referrer_user_id" BIGINT NOT NULL REFERENCES "users" ("id") ON DELETE CASCADE,
            "kind" VARCHAR(32) NOT NULL DEFAULT 'first_payment',
            "payment_id" VARCHAR(128),
            "months" INT,
            "device_count" INT,
            "amount_rub" INT,
            "friend_bonus_days" INT NOT NULL DEFAULT 0,
            "referrer_bonus_days" INT NOT NULL DEFAULT 0,
            "applied_to_subscription" BOOLEAN NOT NULL DEFAULT FALSE,
            "created_at" TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE UNIQUE INDEX IF NOT EXISTS "ux_referral_rewards_referred_kind"
            ON "referral_rewards" ("referred_user_id", "kind");
        CREATE INDEX IF NOT EXISTS "ix_referral_rewards_referrer"
            ON "referral_rewards" ("referrer_user_id");
        CREATE INDEX IF NOT EXISTS "ix_referral_rewards_kind"
            ON "referral_rewards" ("kind");
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "referral_rewards";
    """

