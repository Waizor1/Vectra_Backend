from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "push_subscriptions" (
            "id" SERIAL PRIMARY KEY,
            "user_id" BIGINT NOT NULL REFERENCES "users" ("id") ON DELETE CASCADE,
            "endpoint" VARCHAR(2048) NOT NULL UNIQUE,
            "p256dh" VARCHAR(256) NOT NULL,
            "auth" VARCHAR(128) NOT NULL,
            "user_agent" VARCHAR(512),
            "locale" VARCHAR(16),
            "is_active" BOOLEAN NOT NULL DEFAULT TRUE,
            "failure_count" INT NOT NULL DEFAULT 0,
            "last_success_at" TIMESTAMPTZ,
            "last_failure_at" TIMESTAMPTZ,
            "last_error" VARCHAR(512),
            "created_at" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            "updated_at" TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE INDEX IF NOT EXISTS "ix_push_subscriptions_user_active"
            ON "push_subscriptions" ("user_id", "is_active");
        CREATE INDEX IF NOT EXISTS "ix_push_subscriptions_active"
            ON "push_subscriptions" ("is_active");
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "push_subscriptions";
    """
