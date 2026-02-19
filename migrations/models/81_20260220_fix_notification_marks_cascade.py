from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
    ALTER TABLE "notification_marks"
        DROP CONSTRAINT IF EXISTS "notification_marks_user_id_fkey";
    ALTER TABLE "notification_marks"
        ADD CONSTRAINT "notification_marks_user_id_fkey"
        FOREIGN KEY ("user_id") REFERENCES "users" ("id") ON DELETE CASCADE;
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
    ALTER TABLE "notification_marks"
        DROP CONSTRAINT IF EXISTS "notification_marks_user_id_fkey";
    ALTER TABLE "notification_marks"
        ADD CONSTRAINT "notification_marks_user_id_fkey"
        FOREIGN KEY ("user_id") REFERENCES "users" ("id");
    """
