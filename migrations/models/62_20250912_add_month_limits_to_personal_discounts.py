from tortoise import BaseDBAsyncClient  # type: ignore


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "personal_discounts" ADD COLUMN IF NOT EXISTS "min_months" INT;
        ALTER TABLE "personal_discounts" ADD COLUMN IF NOT EXISTS "max_months" INT;
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "personal_discounts" DROP COLUMN IF EXISTS "min_months";
        ALTER TABLE "personal_discounts" DROP COLUMN IF EXISTS "max_months";
    """

