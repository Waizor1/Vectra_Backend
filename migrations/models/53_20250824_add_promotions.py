from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        -- Promo batches
        CREATE TABLE IF NOT EXISTS "promo_batches" (
            "id" SERIAL NOT NULL PRIMARY KEY,
            "title" VARCHAR(255) NOT NULL,
            "notes" TEXT,
            "created_at" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            "created_by_id" INT
        );
        CREATE INDEX IF NOT EXISTS "idx_promo_batches_created_by" ON "promo_batches" ("created_by_id");

        -- Promo codes
        CREATE TABLE IF NOT EXISTS "promo_codes" (
            "id" SERIAL NOT NULL PRIMARY KEY,
            "batch_id" INT,
            "code_hmac" VARCHAR(128) NOT NULL UNIQUE,
            "effects" JSONB NOT NULL DEFAULT '{}'::jsonb,
            "max_activations" INT NOT NULL DEFAULT 1,
            "per_user_limit" INT NOT NULL DEFAULT 1,
            "expires_at" DATE,
            "disabled" BOOL NOT NULL DEFAULT FALSE,
            "created_at" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT "fk_promo_codes_batch" FOREIGN KEY ("batch_id") REFERENCES "promo_batches" ("id") ON DELETE SET NULL
        );
        CREATE INDEX IF NOT EXISTS "idx_promo_codes_batch_id" ON "promo_codes" ("batch_id");

        -- Promo usages
        CREATE TABLE IF NOT EXISTS "promo_usages" (
            "id" SERIAL NOT NULL PRIMARY KEY,
            "promo_code_id" INT NOT NULL,
            "user_id" BIGINT NOT NULL,
            "used_at" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            "context" JSONB NOT NULL DEFAULT '{}'::jsonb,
            CONSTRAINT "fk_promo_usages_code" FOREIGN KEY ("promo_code_id") REFERENCES "promo_codes" ("id") ON DELETE CASCADE,
            CONSTRAINT "fk_promo_usages_user" FOREIGN KEY ("user_id") REFERENCES "users" ("id") ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS "idx_promo_usages_code" ON "promo_usages" ("promo_code_id");
        CREATE INDEX IF NOT EXISTS "idx_promo_usages_user" ON "promo_usages" ("user_id");
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "promo_usages";
        DROP TABLE IF EXISTS "promo_codes";
        DROP TABLE IF EXISTS "promo_batches";
    """