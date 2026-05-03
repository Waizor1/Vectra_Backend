from tortoise import BaseDBAsyncClient  # type: ignore


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "users"
            ADD COLUMN IF NOT EXISTS "email_notifications_enabled" BOOLEAN NOT NULL DEFAULT TRUE;
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "users"
            DROP COLUMN IF EXISTS "email_notifications_enabled";
    """
