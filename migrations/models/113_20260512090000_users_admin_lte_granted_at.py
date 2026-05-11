"""Capture the moment an admin granted a personal LTE quota.

`users.lte_gb_total` is an admin-override that lives forever; the LTE
limiter and `/active_tariff` previously anchored the quota window to
`trial_started_at or created_at`. For users whose `trial_started_at` is
NULL (and `created_at` is months/years in the past) the limiter saw a
full lifetime worth of traffic on day one of the admin grant and
disabled the squad immediately.

The new column records when the admin grant happened so the quota
window can start at the grant moment instead of the legacy
registration anchor. Nullable: legacy admin-grant rows existed before
this column did, and the runtime fallback chain
`trial_started_at -> admin_lte_granted_at -> created_at` keeps the old
behaviour for those rows (no retroactive correction).

Idempotent so it is safe under `SCHEMA_INIT_GENERATE_ONLY=true`
bootstrap. ADD COLUMN of a NULL-able column takes only an ACCESS
EXCLUSIVE lock briefly to update the catalog — no table rewrite.
"""

from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "users"
            ADD COLUMN IF NOT EXISTS "admin_lte_granted_at" TIMESTAMPTZ;
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "users"
            DROP COLUMN IF EXISTS "admin_lte_granted_at";
    """
