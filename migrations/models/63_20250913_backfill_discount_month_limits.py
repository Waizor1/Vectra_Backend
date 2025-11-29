from tortoise import BaseDBAsyncClient  # type: ignore


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        UPDATE personal_discounts pd
        SET
            min_months = COALESCE(
                pd.min_months,
                NULLIF((promo.effects->>'min_months')::int, 0)
            ),
            max_months = COALESCE(
                pd.max_months,
                NULLIF((promo.effects->>'max_months')::int, 0)
            )
        FROM promo_codes promo
        WHERE pd.source = 'promo'
          AND pd.metadata ? 'promo_id'
          AND promo.id::text = pd.metadata->>'promo_id'
          AND (pd.min_months IS NULL OR pd.max_months IS NULL);
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        UPDATE personal_discounts
        SET min_months = NULL, max_months = NULL
        WHERE source = 'promo' AND (min_months IS NOT NULL OR max_months IS NOT NULL);
    """

