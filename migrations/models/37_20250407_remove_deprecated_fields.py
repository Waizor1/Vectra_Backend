from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "users" DROP COLUMN IF EXISTS "connect_url";
        ALTER TABLE "users" DROP COLUMN IF EXISTS "tv_connect";
        ALTER TABLE "users" DROP COLUMN IF EXISTS "is_sended_notification_connect";
        ALTER TABLE "users" DROP COLUMN IF EXISTS "last_action";
        DROP TABLE IF EXISTS "tv_code";
        """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "users" ADD "connect_url" VARCHAR(100);
        ALTER TABLE "users" ADD "tv_connect" VARCHAR(5);
        ALTER TABLE "users" ADD "is_sended_notification_connect" BOOLEAN NOT NULL DEFAULT False;
        ALTER TABLE "users" ADD "last_action" VARCHAR(100);
        CREATE TABLE IF NOT EXISTS "tv_code" (
            "id" SERIAL NOT NULL PRIMARY KEY,
            "code" VARCHAR(20) NOT NULL,
            "connect_url" VARCHAR(100) NOT NULL
        );
        """ 