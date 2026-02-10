from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "users" ADD COLUMN IF NOT EXISTS "referral_bonus_days_total" INT NOT NULL DEFAULT 0;
        ALTER TABLE "users" ADD COLUMN IF NOT EXISTS "referral_first_payment_rewarded" BOOLEAN NOT NULL DEFAULT FALSE;
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "users" DROP COLUMN IF EXISTS "referral_first_payment_rewarded";
        ALTER TABLE "users" DROP COLUMN IF EXISTS "referral_bonus_days_total";
    """

