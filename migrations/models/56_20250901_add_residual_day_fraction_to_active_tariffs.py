from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "active_tariffs" ADD "residual_day_fraction" DOUBLE PRECISION;
        UPDATE "active_tariffs" SET "residual_day_fraction" = 0.0 WHERE "residual_day_fraction" IS NULL;
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "active_tariffs" DROP COLUMN "residual_day_fraction";
    """


