"""Create home_screen_install_signals table for the PWA-install funnel
ledger.

Indexes are added separately via `_apply_concurrent_index_patches` in
`__main__.py` so they can use CREATE INDEX CONCURRENTLY outside the
migration transaction (lint requirement, see
scripts/check_migration_safety.py). The table itself is brand new and
empty on first deploy so the CREATE TABLE is safe inside the migration
transaction.
"""

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
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP INDEX IF EXISTS "ix_hs_signals_verdict";
        DROP INDEX IF EXISTS "ix_hs_signals_created";
        DROP INDEX IF EXISTS "ix_hs_signals_user";
        DROP TABLE IF EXISTS "home_screen_install_signals";
    """
