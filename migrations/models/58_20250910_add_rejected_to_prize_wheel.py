from tortoise import BaseDBAsyncClient  # type: ignore


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "prize_wheel_history"
        ADD COLUMN IF NOT EXISTS "is_rejected" BOOL NOT NULL DEFAULT FALSE;
        ALTER TABLE "prize_wheel_history"
        ADD COLUMN IF NOT EXISTS "rejected_at" TIMESTAMPTZ;
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "prize_wheel_history" DROP COLUMN IF EXISTS "is_rejected";
        ALTER TABLE "prize_wheel_history" DROP COLUMN IF EXISTS "rejected_at";
    """


