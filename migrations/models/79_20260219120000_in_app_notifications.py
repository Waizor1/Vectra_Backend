"""In-app notifications tables: in_app_notifications, notification_views.

Idempotent: CREATE TABLE IF NOT EXISTS, CREATE INDEX IF NOT EXISTS.
"""
from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "in_app_notifications" (
            "id" SERIAL NOT NULL PRIMARY KEY,
            "title" VARCHAR(255) NOT NULL,
            "body" TEXT NOT NULL,
            "start_at" TIMESTAMPTZ NOT NULL,
            "end_at" TIMESTAMPTZ NOT NULL,
            "max_per_user" INT,
            "max_per_session" INT,
            "auto_hide_seconds" INT,
            "is_active" BOOL NOT NULL DEFAULT TRUE,
            "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            "updated_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS "notification_views" (
            "id" SERIAL NOT NULL PRIMARY KEY,
            "user_id" BIGINT NOT NULL REFERENCES "users" ("id") ON DELETE CASCADE,
            "notification_id" INT NOT NULL REFERENCES "in_app_notifications" ("id") ON DELETE CASCADE,
            "session_id" VARCHAR(128) NOT NULL,
            "viewed_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS "idx_notification_views_user_notif" ON "notification_views" ("user_id", "notification_id");
        CREATE INDEX IF NOT EXISTS "idx_notification_views_user_notif_session" ON "notification_views" ("user_id", "notification_id", "session_id");
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    # Drop in reverse dependency order: notification_views (FK to in_app_notifications) first.
    return """
        DROP TABLE IF EXISTS "notification_views";
        DROP TABLE IF EXISTS "in_app_notifications";
    """
