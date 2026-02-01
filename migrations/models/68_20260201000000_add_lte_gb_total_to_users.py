from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "users" ADD "lte_gb_total" INT;
        UPDATE "users" u
        SET "lte_gb_total" = at."lte_gb_total"
        FROM "active_tariffs" at
        WHERE u."active_tariff_id" = at."id";
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "users" DROP COLUMN "lte_gb_total";
    """
