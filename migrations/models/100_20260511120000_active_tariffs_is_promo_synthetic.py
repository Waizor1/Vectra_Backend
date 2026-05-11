from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "active_tariffs"
            ADD COLUMN IF NOT EXISTS "is_promo_synthetic" BOOLEAN NOT NULL DEFAULT FALSE;
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "active_tariffs" DROP COLUMN IF EXISTS "is_promo_synthetic";
    """
