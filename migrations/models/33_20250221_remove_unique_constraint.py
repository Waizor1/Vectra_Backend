from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE IF EXISTS "tariffs" DROP CONSTRAINT IF EXISTS "unique_months";
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "tariffs" ADD CONSTRAINT "unique_months" UNIQUE ("months");
    """ 