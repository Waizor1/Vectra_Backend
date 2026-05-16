from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "home_screen_install_signals" (
            "id" SERIAL PRIMARY KEY,
            "user_id" BIGINT NOT NULL REFERENCES "users" ("id") ON DELETE CASCADE,
            "trigger" VARCHAR(32) NOT NULL,
            "platform_hint" VARCHAR(32),
            "reward_kind" VARCHAR(16) NOT NULL,
            "had_active_push_sub" BOOLEAN NOT NULL DEFAULT FALSE,
            "verdict" VARCHAR(32) NOT NULL,
            "already_claimed" BOOLEAN NOT NULL DEFAULT FALSE,
            "created_at" TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE INDEX IF NOT EXISTS "ix_hs_signals_user"
            ON "home_screen_install_signals" ("user_id");
        CREATE INDEX IF NOT EXISTS "ix_hs_signals_created"
            ON "home_screen_install_signals" ("created_at");
        CREATE INDEX IF NOT EXISTS "ix_hs_signals_verdict"
            ON "home_screen_install_signals" ("verdict");
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "home_screen_install_signals";
    """
