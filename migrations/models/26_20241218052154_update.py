from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "tvcode" ADD "connect_url" VARCHAR(100) NOT NULL;
        ALTER TABLE "tvcode" DROP COLUMN "user_id";"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "tvcode" ADD "user_id" BIGINT NOT NULL;
        ALTER TABLE "tvcode" DROP COLUMN "connect_url";"""
