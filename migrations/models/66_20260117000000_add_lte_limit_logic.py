from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "tariffs" ADD "lte_enabled" BOOLEAN NOT NULL DEFAULT FALSE;
        ALTER TABLE "tariffs" ADD "lte_price_per_gb" DOUBLE PRECISION NOT NULL DEFAULT 0;

        ALTER TABLE "active_tariffs" ADD "lte_gb_total" INT NOT NULL DEFAULT 0;
        ALTER TABLE "active_tariffs" ADD "lte_gb_used" DOUBLE PRECISION NOT NULL DEFAULT 0;
        ALTER TABLE "active_tariffs" ADD "lte_price_per_gb" DOUBLE PRECISION NOT NULL DEFAULT 0;
        ALTER TABLE "active_tariffs" ADD "lte_usage_last_date" DATE;
        ALTER TABLE "active_tariffs" ADD "lte_usage_last_total_gb" DOUBLE PRECISION NOT NULL DEFAULT 0;

        UPDATE "tariffs"
        SET "lte_enabled" = TRUE,
            "lte_price_per_gb" = 1.5;

        UPDATE "active_tariffs"
        SET "lte_gb_total" = 30,
            "lte_gb_used" = 0,
            "lte_price_per_gb" = 1.5,
            "lte_usage_last_date" = NULL,
            "lte_usage_last_total_gb" = 0;
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "active_tariffs" DROP COLUMN "lte_usage_last_total_gb";
        ALTER TABLE "active_tariffs" DROP COLUMN "lte_usage_last_date";
        ALTER TABLE "active_tariffs" DROP COLUMN "lte_price_per_gb";
        ALTER TABLE "active_tariffs" DROP COLUMN "lte_gb_used";
        ALTER TABLE "active_tariffs" DROP COLUMN "lte_gb_total";

        ALTER TABLE "tariffs" DROP COLUMN "lte_price_per_gb";
        ALTER TABLE "tariffs" DROP COLUMN "lte_enabled";
    """
