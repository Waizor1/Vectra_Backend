"""Per-partner toggle: which link form to render in the partner cabinet.

Stores `'bot'` (open Telegram bot chat with `?start=<payload>`) or `'app'`
(open Mini App directly via `?startapp=<payload>`). Default `'bot'` for new
rows captures cold traffic — the same reasoning as the `/utm` admin command
fix from 2026-05-07. Existing rows backfill to `'bot'` too: the Telegram
start_param is identical, so already-shared links keep working regardless
of which URL form was printed.

Idempotent so it is safe under SCHEMA_INIT_GENERATE_ONLY=true startup bootstrap.
"""

from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "users"
            ADD COLUMN IF NOT EXISTS "partner_link_mode" VARCHAR(8) NOT NULL DEFAULT 'bot';
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "users"
            DROP COLUMN IF EXISTS "partner_link_mode";
    """
