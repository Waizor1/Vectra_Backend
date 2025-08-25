from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "promo_codes" ADD COLUMN IF NOT EXISTS "name" VARCHAR(255);
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "promo_codes" DROP COLUMN IF EXISTS "name";
    """


