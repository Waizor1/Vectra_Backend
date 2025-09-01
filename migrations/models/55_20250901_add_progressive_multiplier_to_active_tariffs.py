from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "active_tariffs" ADD "progressive_multiplier" DOUBLE PRECISION;
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "active_tariffs" DROP COLUMN "progressive_multiplier";
    """


