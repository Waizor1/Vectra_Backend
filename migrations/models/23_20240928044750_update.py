from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "users" ADD "renew_id" VARCHAR(100);
        ALTER TABLE "users" ALTER COLUMN "expired_at" SET DEFAULT '2024-10-01';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "users" DROP COLUMN "renew_id";
        ALTER TABLE "users" ALTER COLUMN "expired_at" SET DEFAULT '2024-09-30';"""
