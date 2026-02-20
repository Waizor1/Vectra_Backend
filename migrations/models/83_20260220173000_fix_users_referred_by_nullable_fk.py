from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
    ALTER TABLE "users"
        ALTER COLUMN "referred_by" DROP DEFAULT;

    ALTER TABLE "users"
        ALTER COLUMN "referred_by" DROP NOT NULL;

    UPDATE "users"
    SET "referred_by" = NULL
    WHERE "referred_by" = 0;
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
    UPDATE "users"
    SET "referred_by" = 0
    WHERE "referred_by" IS NULL;

    ALTER TABLE "users"
        ALTER COLUMN "referred_by" SET DEFAULT 0;

    ALTER TABLE "users"
        ALTER COLUMN "referred_by" SET NOT NULL;
    """
