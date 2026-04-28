from tortoise import BaseDBAsyncClient  # type: ignore


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "users"
            ADD COLUMN IF NOT EXISTS "key_activated" BOOLEAN NOT NULL DEFAULT FALSE;

        UPDATE "users" u
           SET "key_activated" = TRUE
          FROM "hwid_devices_local" h
         WHERE u."key_activated" = FALSE
           AND u."remnawave_uuid" IS NOT NULL
           AND h."user_uuid" = u."remnawave_uuid";
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "users"
            DROP COLUMN IF EXISTS "key_activated";
    """
