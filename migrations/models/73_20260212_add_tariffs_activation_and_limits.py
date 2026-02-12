from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "tariffs" ADD COLUMN IF NOT EXISTS "is_active" BOOLEAN NOT NULL DEFAULT TRUE;
        ALTER TABLE "tariffs" ADD COLUMN IF NOT EXISTS "devices_limit_default" INT NOT NULL DEFAULT 3;
        ALTER TABLE "tariffs" ADD COLUMN IF NOT EXISTS "devices_limit_family" INT NOT NULL DEFAULT 10;
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "tariffs" DROP COLUMN IF EXISTS "devices_limit_family";
        ALTER TABLE "tariffs" DROP COLUMN IF EXISTS "devices_limit_default";
        ALTER TABLE "tariffs" DROP COLUMN IF EXISTS "is_active";
    """
