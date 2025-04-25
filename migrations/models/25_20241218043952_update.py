from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "tvcode" (
    "id" SERIAL NOT NULL PRIMARY KEY,
    "code" VARCHAR(20) NOT NULL,
    "user_id" BIGINT NOT NULL
);
        DROP TABLE IF EXISTS "tv";"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "tvcode";"""
