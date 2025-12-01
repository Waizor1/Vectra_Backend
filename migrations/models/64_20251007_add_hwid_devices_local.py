from tortoise import BaseDBAsyncClient  # type: ignore


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "hwid_devices_local" (
            "id" SERIAL NOT NULL PRIMARY KEY,
            "hwid" VARCHAR(255) NOT NULL,
            "user_uuid" UUID,
            "telegram_user_id" BIGINT,
            "first_seen_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "last_seen_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE UNIQUE INDEX IF NOT EXISTS "uid_hwid_local_pair" ON "hwid_devices_local" ("hwid", "user_uuid");
        CREATE INDEX IF NOT EXISTS "idx_hwid_local_hwid" ON "hwid_devices_local" ("hwid");
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "hwid_devices_local";
    """
