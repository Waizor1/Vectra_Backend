from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "processed_payments"
            ADD COLUMN IF NOT EXISTS "payment_method" VARCHAR(32);

        CREATE INDEX IF NOT EXISTS "idx_processed_payments_payment_method_processed_at"
            ON "processed_payments" ("payment_method", "processed_at")
            WHERE "payment_method" IS NOT NULL;
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP INDEX IF EXISTS "idx_processed_payments_payment_method_processed_at";

        ALTER TABLE "processed_payments" DROP COLUMN IF EXISTS "payment_method";
    """
