"""Add review_status + review_signals to partner_earnings.

Used to freeze partner cashback when a referral self-dealing signal is detected
(e.g. referrer and referred share a HWID). Frozen rows are surfaced to admins in
the bot for approve/reject. Default 'active' keeps prior rows unchanged.

Idempotent so it is safe under SCHEMA_INIT_GENERATE_ONLY=true startup bootstrap.
"""

from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "partner_earnings"
            ADD COLUMN IF NOT EXISTS "review_status" VARCHAR(24) NOT NULL DEFAULT 'active',
            ADD COLUMN IF NOT EXISTS "review_signals" JSONB NULL;
        CREATE INDEX IF NOT EXISTS "ix_partner_earnings_review_status"
            ON "partner_earnings" ("review_status");
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP INDEX IF EXISTS "ix_partner_earnings_review_status";
        ALTER TABLE "partner_earnings"
            DROP COLUMN IF EXISTS "review_signals",
            DROP COLUMN IF EXISTS "review_status";
    """
