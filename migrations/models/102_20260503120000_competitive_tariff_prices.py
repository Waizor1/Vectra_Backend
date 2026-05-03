from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        UPDATE "tariffs"
           SET "base_price" = CASE "months"
                   WHEN 1 THEN 199
                   WHEN 3 THEN 449
                   WHEN 6 THEN 749
                   WHEN 12 THEN 1299
                   ELSE "base_price"
               END,
               "final_price_default" = CASE "months"
                   WHEN 1 THEN 199
                   WHEN 3 THEN 449
                   WHEN 6 THEN 749
                   WHEN 12 THEN 1299
                   ELSE "final_price_default"
               END,
               "progressive_multiplier" = 0.65,
               "devices_limit_default" = 1,
               "devices_limit_family" = GREATEST(COALESCE("devices_limit_family", 0), 30),
               "family_plan_enabled" = FALSE,
               "storefront_badge" = CASE
                   WHEN "months" = 12 THEN COALESCE("storefront_badge", 'выгодно')
                   ELSE "storefront_badge"
               END
         WHERE "months" IN (1, 3, 6, 12)
           AND COALESCE("is_active", TRUE) = TRUE;
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        UPDATE "tariffs"
           SET "base_price" = CASE "months"
                   WHEN 1 THEN 290
                   WHEN 3 THEN 749
                   WHEN 6 THEN 1290
                   WHEN 12 THEN 2190
                   ELSE "base_price"
               END,
               "final_price_default" = CASE "months"
                   WHEN 1 THEN 290
                   WHEN 3 THEN 749
                   WHEN 6 THEN 1290
                   WHEN 12 THEN 2190
                   ELSE "final_price_default"
               END,
               "progressive_multiplier" = CASE "months"
                   WHEN 1 THEN 0.9
                   WHEN 3 THEN 0.88
                   WHEN 6 THEN 0.86
                   WHEN 12 THEN 0.82
                   ELSE "progressive_multiplier"
               END
         WHERE "months" IN (1, 3, 6, 12)
           AND COALESCE("is_active", TRUE) = TRUE;
    """
