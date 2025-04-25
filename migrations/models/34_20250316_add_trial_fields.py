from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        -- Добавление поля created_at, если оно отсутствует
        ALTER TABLE "users" ADD COLUMN IF NOT EXISTS "created_at" TIMESTAMPTZ;
        
        -- Добавление поля is_trial, если оно отсутствует
        ALTER TABLE "users" ADD COLUMN IF NOT EXISTS "is_trial" BOOLEAN DEFAULT FALSE;
        
        -- Добавление поля used_trial, если оно отсутствует
        ALTER TABLE "users" ADD COLUMN IF NOT EXISTS "used_trial" BOOLEAN DEFAULT FALSE;
        
        -- Добавление поля notification_2h_sent, если оно отсутствует
        ALTER TABLE "users" ADD COLUMN IF NOT EXISTS "notification_2h_sent" BOOLEAN DEFAULT FALSE;
        
        -- Добавление поля notification_24h_sent, если оно отсутствует
        ALTER TABLE "users" ADD COLUMN IF NOT EXISTS "notification_24h_sent" BOOLEAN DEFAULT FALSE;
        
        -- Обновление значения created_at для существующих записей
        UPDATE "users" SET "created_at" = NOW() WHERE "created_at" IS NULL;
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "users" DROP COLUMN IF EXISTS "created_at";
        ALTER TABLE "users" DROP COLUMN IF EXISTS "is_trial";
        ALTER TABLE "users" DROP COLUMN IF EXISTS "used_trial";
        ALTER TABLE "users" DROP COLUMN IF EXISTS "notification_2h_sent";
        ALTER TABLE "users" DROP COLUMN IF EXISTS "notification_24h_sent";
    """ 