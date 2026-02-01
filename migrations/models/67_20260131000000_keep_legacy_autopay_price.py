from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "active_tariffs"
        ADD "lte_autopay_free" BOOLEAN NOT NULL DEFAULT FALSE;

        UPDATE "active_tariffs"
        SET "lte_autopay_free" = TRUE
        WHERE "lte_gb_total" > 0;
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "active_tariffs"
        DROP COLUMN "lte_autopay_free";
    """
