from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "subscription_freezes"
            ADD COLUMN IF NOT EXISTS "base_lte_gb_used" DOUBLE PRECISION;

        -- Keep only one active freeze row per user before adding unique partial index.
        WITH ranked AS (
            SELECT
                id,
                ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY id DESC) AS rn
            FROM "subscription_freezes"
            WHERE "is_active" = TRUE AND "resume_applied" = FALSE
        )
        UPDATE "subscription_freezes" AS sf
        SET
            "is_active" = FALSE,
            "updated_at" = CURRENT_TIMESTAMP
        FROM ranked
        WHERE sf.id = ranked.id AND ranked.rn > 1;

        CREATE UNIQUE INDEX IF NOT EXISTS "ux_subscription_freezes_active_user"
            ON "subscription_freezes" ("user_id")
            WHERE "is_active" = TRUE AND "resume_applied" = FALSE;
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP INDEX IF EXISTS "ux_subscription_freezes_active_user";

        ALTER TABLE "subscription_freezes"
            DROP COLUMN IF EXISTS "base_lte_gb_used";
    """
