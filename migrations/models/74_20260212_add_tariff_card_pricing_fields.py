from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "tariffs" ADD COLUMN IF NOT EXISTS "family_plan_enabled" BOOLEAN NOT NULL DEFAULT TRUE;
        ALTER TABLE "tariffs" ADD COLUMN IF NOT EXISTS "final_price_default" INT;
        ALTER TABLE "tariffs" ADD COLUMN IF NOT EXISTS "final_price_family" INT;
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "tariffs" DROP COLUMN IF EXISTS "final_price_family";
        ALTER TABLE "tariffs" DROP COLUMN IF EXISTS "final_price_default";
        ALTER TABLE "tariffs" DROP COLUMN IF EXISTS "family_plan_enabled";
    """
