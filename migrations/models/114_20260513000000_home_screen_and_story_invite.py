"""Add home-screen install tracking + story-invite trial provenance to users.

This migration adds seven nullable columns to `users` that together back two
related product flows (spec: ai_docs/develop/telegram-webapp-features-spec-2026-05-12.md):

1. Home-screen install reward (idempotent one-time bonus).
   - `home_screen_added_at`        — when the Mini App icon was placed on the
     user's home screen. Set either by the explicit /referrals/home-screen-claim
     endpoint after the Telegram `homeScreenAdded` event, or by the same
     endpoint as a side effect of a successful claim.
   - `home_screen_reward_granted_at` — when the one-time bonus (50 ₽ to balance
     OR 10% next-purchase discount) was paid out. Together with the column
     above this is the idempotency guard; once non-null the endpoint will
     never grant a second bonus to the same user.
   - `home_screen_promo_sent_at`    — last time the bot pushed the
     "install on home screen" promo. Drives the 24h -> 7d -> 30d decay
     schedule in the cron task that delivers the gentle reminder.
   - `home_screen_promo_sent_count` — how many times the cron has sent that
     promo. Stops at 3.

2. Story-invite trial (20d / 1 device / 1 GB LTE) granted to users who arrive
   via a `startapp=story_<code>` deep link from a Telegram Stories share.
   - `invite_source`             — string tag among {'manual', 'story',
     'partner_qr', NULL}. Distinguishes invite provenance so the trial-grant
     branch in Users._grant_trial_if_unclaimed can choose the right
     duration / device / LTE bundle.
   - `invited_by_referrer_id`    — denormalized referrer id captured at
     registration. We already have `referred_by`, but that column is shared
     with the bot /start flow and partner QR codes, which makes it
     unreliable for story-attribution analytics. Storing the referrer
     separately keeps reporting honest.
   - `story_trial_used_at`       — when the user redeemed a story-invite
     trial. Prevents the same user from re-grabbing a story-trial after
     deleting/re-registering (paired with hwid-fingerprint check on
     consume time).

All columns are NULL-able. ADD COLUMN of a NULL-able column takes only a
brief ACCESS EXCLUSIVE lock to update the catalog — no table rewrite, safe
to run on a live production users table.

Idempotent so it is safe under SCHEMA_INIT_GENERATE_ONLY=true bootstrap.
"""

from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    # The three indexes below MUST be created with CREATE INDEX CONCURRENTLY
    # outside the migration transaction (see scripts/check_migration_safety.py).
    # That can't run inside an Aerich-managed BEGIN..COMMIT block, so we add
    # the indexes in a post-migration step:
    #   bloobcat/__main__.py picks up the column DDL here, then the
    #   `_run_concurrent_index_patches` hook (added in this PR) creates the
    #   indexes one-by-one with `SET lock_timeout = '5s'` and AUTOCOMMIT
    #   isolation. That hook is idempotent (`IF NOT EXISTS`) and safe to
    #   re-run on every boot.
    return """
        ALTER TABLE "users"
            ADD COLUMN IF NOT EXISTS "home_screen_added_at" TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS "home_screen_reward_granted_at" TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS "home_screen_promo_sent_at" TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS "home_screen_promo_sent_count" INTEGER NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS "invite_source" VARCHAR(32),
            ADD COLUMN IF NOT EXISTS "invited_by_referrer_id" BIGINT,
            ADD COLUMN IF NOT EXISTS "story_trial_used_at" TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS "story_code" VARCHAR(32);
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP INDEX IF EXISTS "idx_users_story_code_unique";
        DROP INDEX IF EXISTS "idx_users_invite_source";
        DROP INDEX IF EXISTS "idx_users_home_screen_promo_pending";
        ALTER TABLE "users"
            DROP COLUMN IF EXISTS "story_code",
            DROP COLUMN IF EXISTS "story_trial_used_at",
            DROP COLUMN IF EXISTS "invited_by_referrer_id",
            DROP COLUMN IF EXISTS "invite_source",
            DROP COLUMN IF EXISTS "home_screen_promo_sent_count",
            DROP COLUMN IF EXISTS "home_screen_promo_sent_at",
            DROP COLUMN IF EXISTS "home_screen_reward_granted_at",
            DROP COLUMN IF EXISTS "home_screen_added_at";
    """
