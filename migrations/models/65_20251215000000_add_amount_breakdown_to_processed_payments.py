from tortoise import BaseDBAsyncClient  # type: ignore


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "processed_payments"
            ADD COLUMN IF NOT EXISTS "amount_external" DECIMAL(10,2) NOT NULL DEFAULT 0;

        ALTER TABLE "processed_payments"
            ADD COLUMN IF NOT EXISTS "amount_from_balance" DECIMAL(10,2) NOT NULL DEFAULT 0;

        -- Backfill:
        -- - balance_* payments считаем полностью списанными с баланса
        -- - остальные считаем "внешними" (исторически точного split для mixed платежей может не быть)
        UPDATE "processed_payments"
        SET "amount_external" = CASE
                WHEN "payment_id" LIKE 'balance\\_%' THEN 0
                ELSE "amount"
            END,
            "amount_from_balance" = CASE
                WHEN "payment_id" LIKE 'balance\\_%' THEN "amount"
                ELSE 0
            END
        WHERE ("amount_external" = 0 AND "amount_from_balance" = 0);
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "processed_payments" DROP COLUMN IF EXISTS "amount_external";
        ALTER TABLE "processed_payments" DROP COLUMN IF EXISTS "amount_from_balance";
    """


