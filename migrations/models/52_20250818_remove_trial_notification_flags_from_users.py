from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
    ALTER TABLE "users" DROP COLUMN IF EXISTS "notification_2h_sent";
    ALTER TABLE "users" DROP COLUMN IF EXISTS "notification_24h_sent";
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
    ALTER TABLE "users" ADD COLUMN IF NOT EXISTS "notification_2h_sent" BOOLEAN DEFAULT FALSE;
    ALTER TABLE "users" ADD COLUMN IF NOT EXISTS "notification_24h_sent" BOOLEAN DEFAULT FALSE;
    """


