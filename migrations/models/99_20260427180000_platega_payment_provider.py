from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "processed_payments"
            ADD COLUMN IF NOT EXISTS "provider" VARCHAR(32) NOT NULL DEFAULT 'yookassa';

        ALTER TABLE "processed_payments"
            ADD COLUMN IF NOT EXISTS "client_request_id" VARCHAR(100);

        ALTER TABLE "processed_payments"
            ADD COLUMN IF NOT EXISTS "payment_url" TEXT;

        ALTER TABLE "processed_payments"
            ADD COLUMN IF NOT EXISTS "provider_payload" TEXT;

        CREATE INDEX IF NOT EXISTS "idx_processed_payments_provider_status_processed_at"
            ON "processed_payments" ("provider", "status", "processed_at");

        CREATE UNIQUE INDEX IF NOT EXISTS "uidx_processed_payments_provider_user_client_request"
            ON "processed_payments" ("provider", "user_id", "client_request_id")
            WHERE "client_request_id" IS NOT NULL;
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP INDEX IF EXISTS "uidx_processed_payments_provider_user_client_request";
        DROP INDEX IF EXISTS "idx_processed_payments_provider_status_processed_at";

        ALTER TABLE "processed_payments" DROP COLUMN IF EXISTS "provider_payload";
        ALTER TABLE "processed_payments" DROP COLUMN IF EXISTS "payment_url";
        ALTER TABLE "processed_payments" DROP COLUMN IF EXISTS "client_request_id";
        ALTER TABLE "processed_payments" DROP COLUMN IF EXISTS "provider";
    """
