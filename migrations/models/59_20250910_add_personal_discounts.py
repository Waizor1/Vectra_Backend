from tortoise import BaseDBAsyncClient  # type: ignore


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "personal_discounts" (
            "id" SERIAL NOT NULL PRIMARY KEY,
            "user_id" BIGINT NOT NULL,
            "percent" INT NOT NULL,
            "is_permanent" BOOL NOT NULL DEFAULT FALSE,
            "remaining_uses" INT NOT NULL DEFAULT 0,
            "expires_at" DATE,
            "source" VARCHAR(64),
            "metadata" JSONB NOT NULL DEFAULT '{}',
            "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS "idx_personal_discounts_user" ON "personal_discounts" ("user_id");

        CREATE TABLE IF NOT EXISTS "discount_reservations" (
            "id" SERIAL NOT NULL PRIMARY KEY,
            "user_id" BIGINT NOT NULL,
            "discount_id" INT NOT NULL REFERENCES "personal_discounts" ("id") ON DELETE CASCADE,
            "payment_id" VARCHAR(128),
            "status" VARCHAR(16) NOT NULL DEFAULT 'pending',
            "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS "idx_discount_reservations_user" ON "discount_reservations" ("user_id");
        CREATE INDEX IF NOT EXISTS "idx_discount_reservations_payment" ON "discount_reservations" ("payment_id");
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "discount_reservations";
        DROP TABLE IF EXISTS "personal_discounts";
    """


