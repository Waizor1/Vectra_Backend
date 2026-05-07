"""Add UTM fields to partner_qr_codes.

Each partner QR-code stays attribution-stable (payload remains `qr_<uuidhex>`),
but operators can now annotate codes with `utm_source`, `utm_medium`, and
`utm_campaign` so the public link carries marketer-readable parameters and the
panel can show richer per-channel statistics. All columns are nullable so the
migration is backwards-compatible with existing rows.

Idempotent so it is safe under SCHEMA_INIT_GENERATE_ONLY=true startup bootstrap.
"""

from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "partner_qr_codes"
            ADD COLUMN IF NOT EXISTS "utm_source" VARCHAR(64) NULL,
            ADD COLUMN IF NOT EXISTS "utm_medium" VARCHAR(64) NULL,
            ADD COLUMN IF NOT EXISTS "utm_campaign" VARCHAR(120) NULL;
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "partner_qr_codes"
            DROP COLUMN IF EXISTS "utm_campaign",
            DROP COLUMN IF EXISTS "utm_medium",
            DROP COLUMN IF EXISTS "utm_source";
    """
