from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        -- Добавление полей для отслеживания заблокированных пользователей
        ALTER TABLE "users" ADD COLUMN IF NOT EXISTS "is_blocked" BOOLEAN NOT NULL DEFAULT FALSE;
        ALTER TABLE "users" ADD COLUMN IF NOT EXISTS "blocked_at" TIMESTAMPTZ;
        ALTER TABLE "users" ADD COLUMN IF NOT EXISTS "last_failed_message_at" TIMESTAMPTZ;
        ALTER TABLE "users" ADD COLUMN IF NOT EXISTS "failed_message_count" INT NOT NULL DEFAULT 0;
        
        -- Создание индекса для быстрого поиска заблокированных пользователей
        CREATE INDEX IF NOT EXISTS "idx_users_is_blocked" ON "users" ("is_blocked");
        CREATE INDEX IF NOT EXISTS "idx_users_blocked_at" ON "users" ("blocked_at");
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        -- Удаление индексов
        DROP INDEX IF EXISTS "idx_users_blocked_at";
        DROP INDEX IF EXISTS "idx_users_is_blocked";
        
        -- Удаление полей
        ALTER TABLE "users" DROP COLUMN IF EXISTS "failed_message_count";
        ALTER TABLE "users" DROP COLUMN IF EXISTS "last_failed_message_at";
        ALTER TABLE "users" DROP COLUMN IF EXISTS "blocked_at";
        ALTER TABLE "users" DROP COLUMN IF EXISTS "is_blocked";
    """ 