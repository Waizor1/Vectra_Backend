from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "tariffs" ADD "progressive_multiplier" REAL NOT NULL DEFAULT 0.9;
        ALTER TABLE "tariffs" DROP COLUMN "device_discount";
        ALTER TABLE "tariffs" DROP COLUMN "hwid_limit";
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "tariffs" ADD "device_discount" INT NOT NULL DEFAULT 0;
        ALTER TABLE "tariffs" ADD "hwid_limit" INT NOT NULL DEFAULT 1;
        ALTER TABLE "tariffs" DROP COLUMN "progressive_multiplier";
    """ 