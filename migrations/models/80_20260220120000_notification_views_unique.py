"""Add UNIQUE constraint on notification_views (user_id, notification_id, session_id).

Idempotent: uses DO block to add constraint only if not exists.
"""
from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint c
                JOIN pg_class t ON c.conrelid = t.oid
                WHERE t.relname = 'notification_views'
                  AND c.contype = 'u'
                  AND c.conname = 'ux_notification_views_user_notif_session'
            ) THEN
                ALTER TABLE "notification_views"
                ADD CONSTRAINT "ux_notification_views_user_notif_session"
                UNIQUE ("user_id", "notification_id", "session_id");
            END IF;
        END $$;
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "notification_views"
        DROP CONSTRAINT IF EXISTS "ux_notification_views_user_notif_session";
    """
