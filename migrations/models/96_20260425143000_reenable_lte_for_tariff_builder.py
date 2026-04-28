from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        UPDATE "tariffs"
           SET "lte_enabled" = TRUE,
               "lte_price_per_gb" = CASE
                   WHEN COALESCE("lte_price_per_gb", 0) > 0 THEN "lte_price_per_gb"
                   ELSE 1.5
               END,
               "lte_min_gb" = GREATEST(COALESCE("lte_min_gb", 0), 0),
               "lte_max_gb" = GREATEST(COALESCE("lte_max_gb", 0), 500),
               "lte_step_gb" = GREATEST(COALESCE("lte_step_gb", 0), 1)
         WHERE "months" IN (1, 3, 6, 12);
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        UPDATE "tariffs"
           SET "lte_enabled" = FALSE
         WHERE "months" IN (1, 3, 6, 12);
    """
