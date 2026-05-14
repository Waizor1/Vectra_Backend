"""Create push_subscriptions table for PWA Web Push delivery.

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
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP INDEX IF EXISTS "idx_push_subscriptions_user_active";
        DROP INDEX IF EXISTS "idx_push_subscriptions_active";
        DROP TABLE IF EXISTS "push_subscriptions";
    """
