"""Create reverse_trial_states table for the 7-day reverse trial backend.

The migration body only creates the empty table — indexes are added
separately via `_apply_concurrent_index_patches` in `bloobcat/__main__.py`
so they can run with CREATE INDEX CONCURRENTLY outside the migration
transaction. That is the policy enforced by `scripts/check_migration_safety.py`
for any migration with prefix > 107. The table itself is brand new and
empty on first deploy so the CREATE TABLE is safe inside the migration
transaction.
"""

from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "reverse_trial_states" (
            "id" SERIAL PRIMARY KEY,
            "user_id" BIGINT NOT NULL UNIQUE REFERENCES "users" ("id") ON DELETE CASCADE,
            "granted_at" TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            "expires_at" TIMESTAMPTZ NOT NULL,
            "status" VARCHAR(24) NOT NULL DEFAULT 'active',
            "tariff_sku_snapshot" VARCHAR(64),
            "tariff_active_id_snapshot" VARCHAR(5),
            "discount_personal_id" INTEGER,
            "discount_used_at" TIMESTAMPTZ,
            "downgraded_at" TIMESTAMPTZ,
            "pre_warning_sent_at" TIMESTAMPTZ,
            "created_at" TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP INDEX IF EXISTS "ix_reverse_trial_states_user";
        DROP INDEX IF EXISTS "ix_reverse_trial_states_status_expires";
        DROP TABLE IF EXISTS "reverse_trial_states";
    """
