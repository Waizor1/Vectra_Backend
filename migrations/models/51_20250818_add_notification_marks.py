from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
    CREATE TABLE IF NOT EXISTS "notification_marks" (
        "id" SERIAL PRIMARY KEY,
        "type" VARCHAR(64) NOT NULL,
        "key" VARCHAR(64),
        "meta" VARCHAR(255),
        "sent_at" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        "user_id" BIGINT NOT NULL REFERENCES "users" ("id") ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS "idx_notification_marks_user" ON "notification_marks" ("user_id");
    CREATE INDEX IF NOT EXISTS "idx_notification_marks_type_key" ON "notification_marks" ("type", "key");

    -- Backfill from existing flags: trial 2h/24h
    INSERT INTO "notification_marks" (user_id, type, key, sent_at)
    SELECT id, 'trial_no_sub', '2h', registration_date + INTERVAL '2 hour'
    FROM users
    WHERE notification_2h_sent = TRUE
    ON CONFLICT DO NOTHING;

    INSERT INTO "notification_marks" (user_id, type, key, sent_at)
    SELECT id, 'trial_no_sub', '24h', registration_date + INTERVAL '24 hour'
    FROM users
    WHERE notification_24h_sent = TRUE
    ON CONFLICT DO NOTHING;
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
    DROP TABLE IF EXISTS "notification_marks";
    """


