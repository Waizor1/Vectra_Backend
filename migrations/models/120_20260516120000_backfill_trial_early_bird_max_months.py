"""Backfill: clear `max_months=1` on existing `trial_early_bird` discounts.

PR #95 (1.92.0) created the trial early-bird PersonalDiscount with a hard
`max_months=1` cap. PR #100 (1.97.0) dropped that cap from new rows so the
−50 % applies across all 1/3/6/12-month tariffs, restoring a monotonic
price-per-month curve.

Rows already inserted under PR #95 still carry `max_months=1` and would
keep the bug for any trial user who has not yet paid. This migration
clears the constraint on those rows so they behave consistently with
new grants — fully idempotent, narrow `WHERE` predicate, fully
reversible (just set them back to 1).

Touches at most the count of distinct trial users who got the early-bird
discount in production since PR #95 landed (≈ a few thousand at most),
on a single small table — runs in well under a second, no lock concern.
"""

from tortoise import BaseDBAsyncClient


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        UPDATE "personal_discounts"
        SET "max_months" = NULL
        WHERE "source" = 'trial_early_bird'
          AND "max_months" = 1;
    """


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        UPDATE "personal_discounts"
        SET "max_months" = 1
        WHERE "source" = 'trial_early_bird'
          AND "max_months" IS NULL;
    """
