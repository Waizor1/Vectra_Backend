from tortoise import BaseDBAsyncClient # type: ignore


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "processed_payments" (
            "id" SERIAL NOT NULL PRIMARY KEY,
            "payment_id" VARCHAR(100) NOT NULL UNIQUE,
            "user_id" BIGINT NOT NULL,
            "amount" DECIMAL(10,2) NOT NULL,
            "processed_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "status" VARCHAR(50) NOT NULL
        );
        CREATE INDEX "idx_processed_payments_payment_id" ON "processed_payments" ("payment_id");
        CREATE INDEX "idx_processed_payments_user_id" ON "processed_payments" ("user_id");
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "processed_payments";
    """ 