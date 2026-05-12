"""Add auth_merge_preview_tokens for two-step account-merge confirmation.

Idempotent. Safe to run on a database that already has the table (the IF NOT
EXISTS guards on table and indexes make this a no-op in that case).
"""

from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    # Brand-new table. Unique constraint on token_hash provides the only
    # lookup path the runtime uses (preview + confirm both query by
    # token_hash). Helper indexes on (winner, loser, expires_at) from the
    # ORM model are deliberately not created here; if sweeping by
    # expires_at ever matters, ship a follow-up migration with the
    # CONCURRENTLY-prefixed index syntax.
    return """
        CREATE TABLE IF NOT EXISTS "auth_merge_preview_tokens" (
            "id" SERIAL PRIMARY KEY,
            "token_hash" VARCHAR(128) NOT NULL UNIQUE,
            "winner_user_id" BIGINT NOT NULL,
            "loser_user_id" BIGINT NOT NULL,
            "provider" VARCHAR(32) NOT NULL,
            "initiated_by_user_id" BIGINT NOT NULL,
            "expires_at" TIMESTAMPTZ NOT NULL,
            "consumed_at" TIMESTAMPTZ,
            "created_at" TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "auth_merge_preview_tokens";
    """
