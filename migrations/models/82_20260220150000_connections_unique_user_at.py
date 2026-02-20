from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
    DELETE FROM "connections" a
    USING "connections" b
    WHERE a.ctid < b.ctid
      AND a."user_id" = b."user_id"
      AND a."at" = b."at";

    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1
            FROM pg_constraint
            WHERE conname = 'connections_user_id_at_unique'
        ) THEN
            ALTER TABLE "connections"
                ADD CONSTRAINT "connections_user_id_at_unique" UNIQUE ("user_id", "at");
        END IF;
    END $$;
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
    ALTER TABLE "connections"
        DROP CONSTRAINT IF EXISTS "connections_user_id_at_unique";
    """
