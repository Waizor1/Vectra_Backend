from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        -- Добавляем поле hwid_limit с дефолтным значением 1
        ALTER TABLE "tariffs" ADD "hwid_limit" INT NOT NULL DEFAULT 1;
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "tariffs" DROP COLUMN "hwid_limit";
    """ 