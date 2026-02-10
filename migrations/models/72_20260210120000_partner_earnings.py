from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "partner_earnings" (
    "id" UUID NOT NULL  PRIMARY KEY,
    "payment_id" VARCHAR(100) NOT NULL UNIQUE,
    "referral_id" BIGINT NOT NULL,
    "source" VARCHAR(24) NOT NULL DEFAULT 'unknown',
    "amount_total_rub" INT NOT NULL,
    "reward_rub" INT NOT NULL,
    "percent" INT NOT NULL,
    "created_at" TIMESTAMPTZ NOT NULL  DEFAULT CURRENT_TIMESTAMP,
    "partner_id" BIGINT NOT NULL REFERENCES "users" ("id") ON DELETE CASCADE,
    "qr_code_id" UUID REFERENCES "partner_qr_codes" ("id") ON DELETE SET NULL
);
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "partner_earnings";
    """

