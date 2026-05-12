from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "users"
            ADD COLUMN IF NOT EXISTS "happ_cryptolink_v5" TEXT,
            ADD COLUMN IF NOT EXISTS "happ_cryptolink_v5_at" TIMESTAMPTZ;

        ALTER TABLE "user_devices"
            ADD COLUMN IF NOT EXISTS "happ_cryptolink_v5" TEXT,
            ADD COLUMN IF NOT EXISTS "happ_cryptolink_v5_at" TIMESTAMPTZ;

        ALTER TABLE "family_members"
            ADD COLUMN IF NOT EXISTS "happ_cryptolink_v5" TEXT,
            ADD COLUMN IF NOT EXISTS "happ_cryptolink_v5_at" TIMESTAMPTZ;
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "users"
            DROP COLUMN IF EXISTS "happ_cryptolink_v5",
            DROP COLUMN IF EXISTS "happ_cryptolink_v5_at";

        ALTER TABLE "user_devices"
            DROP COLUMN IF EXISTS "happ_cryptolink_v5",
            DROP COLUMN IF EXISTS "happ_cryptolink_v5_at";

        ALTER TABLE "family_members"
            DROP COLUMN IF EXISTS "happ_cryptolink_v5",
            DROP COLUMN IF EXISTS "happ_cryptolink_v5_at";
    """
