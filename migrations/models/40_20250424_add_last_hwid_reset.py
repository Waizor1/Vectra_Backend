from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        -- Добавление поля last_hwid_reset для отслеживания времени ручного сброса HWID устройств
        ALTER TABLE "users" ADD COLUMN IF NOT EXISTS "last_hwid_reset" TIMESTAMPTZ;
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        -- Удаление поля last_hwid_reset
        ALTER TABLE "users" DROP COLUMN IF EXISTS "last_hwid_reset";
    """ 