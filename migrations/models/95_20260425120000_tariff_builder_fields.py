from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "tariffs" ADD COLUMN IF NOT EXISTS "lte_min_gb" INT NOT NULL DEFAULT 0;
        ALTER TABLE "tariffs" ADD COLUMN IF NOT EXISTS "lte_max_gb" INT NOT NULL DEFAULT 500;
        ALTER TABLE "tariffs" ADD COLUMN IF NOT EXISTS "lte_step_gb" INT NOT NULL DEFAULT 1;
        ALTER TABLE "tariffs" ADD COLUMN IF NOT EXISTS "storefront_badge" VARCHAR(64);
        ALTER TABLE "tariffs" ADD COLUMN IF NOT EXISTS "storefront_hint" VARCHAR(255);
        UPDATE "tariffs"
           SET "devices_limit_default" = 1,
               "devices_limit_family" = GREATEST(COALESCE("devices_limit_family", 0), 30),
               "family_plan_enabled" = FALSE,
               "final_price_default" = CASE
                   WHEN COALESCE("base_price", 0) > 0 THEN "base_price"
                   ELSE "final_price_default"
               END,
               "lte_max_gb" = GREATEST(COALESCE("lte_max_gb", 0), 500),
               "lte_step_gb" = GREATEST(COALESCE("lte_step_gb", 0), 1)
         WHERE "months" IN (1, 3, 6, 12);
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "tariffs" DROP COLUMN IF EXISTS "storefront_hint";
        ALTER TABLE "tariffs" DROP COLUMN IF EXISTS "storefront_badge";
        ALTER TABLE "tariffs" DROP COLUMN IF EXISTS "lte_step_gb";
        ALTER TABLE "tariffs" DROP COLUMN IF EXISTS "lte_max_gb";
        ALTER TABLE "tariffs" DROP COLUMN IF EXISTS "lte_min_gb";
    """
