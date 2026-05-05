from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "user_devices" (
            "id" SERIAL PRIMARY KEY,
            "user_id" BIGINT NOT NULL REFERENCES "users" ("id") ON DELETE CASCADE,
            "family_member_id" UUID REFERENCES "family_members" ("id") ON DELETE CASCADE,
            "kind" VARCHAR(16) NOT NULL,
            "remnawave_uuid" UUID,
            "hwid" VARCHAR(255),
            "device_name" VARCHAR(128),
            "platform" VARCHAR(64),
            "device_model" VARCHAR(128),
            "os_version" VARCHAR(64),
            "metadata" JSONB,
            "meta_refreshed_at" TIMESTAMPTZ,
            "created_at" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            "updated_at" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            "last_online_at" TIMESTAMPTZ
        );

        CREATE INDEX IF NOT EXISTS "idx_user_devices_user_id"
            ON "user_devices" ("user_id");
        CREATE INDEX IF NOT EXISTS "idx_user_devices_family_member_id"
            ON "user_devices" ("family_member_id");
        CREATE INDEX IF NOT EXISTS "idx_user_devices_remnawave_uuid"
            ON "user_devices" ("remnawave_uuid");
        CREATE INDEX IF NOT EXISTS "idx_user_devices_hwid"
            ON "user_devices" ("hwid");

        ALTER TABLE "users"
            ADD COLUMN IF NOT EXISTS "device_per_user_enabled" BOOLEAN;
        ALTER TABLE "users"
            ADD COLUMN IF NOT EXISTS "temp_setup_token" VARCHAR(255);
        ALTER TABLE "users"
            ADD COLUMN IF NOT EXISTS "temp_setup_expires_at" TIMESTAMPTZ;
        ALTER TABLE "users"
            ADD COLUMN IF NOT EXISTS "temp_setup_device_id" INT;
        CREATE UNIQUE INDEX IF NOT EXISTS "uid_users_temp_setup_token"
            ON "users" ("temp_setup_token");
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP INDEX IF EXISTS "uid_users_temp_setup_token";
        ALTER TABLE "users" DROP COLUMN IF EXISTS "temp_setup_device_id";
        ALTER TABLE "users" DROP COLUMN IF EXISTS "temp_setup_expires_at";
        ALTER TABLE "users" DROP COLUMN IF EXISTS "temp_setup_token";
        ALTER TABLE "users" DROP COLUMN IF EXISTS "device_per_user_enabled";

        DROP INDEX IF EXISTS "idx_user_devices_hwid";
        DROP INDEX IF EXISTS "idx_user_devices_remnawave_uuid";
        DROP INDEX IF EXISTS "idx_user_devices_family_member_id";
        DROP INDEX IF EXISTS "idx_user_devices_user_id";
        DROP TABLE IF EXISTS "user_devices";
    """
