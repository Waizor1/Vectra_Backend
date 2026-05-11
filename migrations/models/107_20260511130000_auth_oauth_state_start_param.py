"""Persist the captured ?start=… on the OAuth state row.

Before this column existed, web OAuth registrations applied UTM/referrer
only inside `/auth/complete-registration`, which runs AFTER the
`notify_web_oauth_registration` Telegram alert. Every OAuth new-user
notification therefore reported the user without UTM, even when they
came in from a tracked link (the rutracker referral case observed for
user id 8004871354643957 on 2026-05-11). Now `/auth/oauth/{provider}/start`
persists the start_param, the OAuth callback applies attribution at
user-creation time, and the notification can include the UTM and the
referrer id.

Idempotent so it is safe under SCHEMA_INIT_GENERATE_ONLY=true startup
bootstrap. Nullable on purpose: callbacks created before the column
existed (and direct typed-URL traffic that has no start_param) must
keep working.
"""

from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "auth_oauth_states"
            ADD COLUMN IF NOT EXISTS "start_param" VARCHAR(256);
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        ALTER TABLE "auth_oauth_states"
            DROP COLUMN IF EXISTS "start_param";
    """
