from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "tariffs" ADD "device_discount" INT NOT NULL DEFAULT 0;
        ALTER TABLE "tariffs" RENAME COLUMN "price" TO "base_price";
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "tariffs" RENAME COLUMN "base_price" TO "price";
        ALTER TABLE "tariffs" DROP COLUMN "device_discount";
    """ 