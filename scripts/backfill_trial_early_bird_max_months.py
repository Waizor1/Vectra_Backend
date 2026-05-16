"""Backfill `personal_discounts.max_months` for existing trial early-bird rows.

History: the trial early-bird discount used to be created with `max_months=1`,
which restricted the −50% stimulus to 1-month subscriptions only. As a result
trial users saw illogical per-month pricing on the subscription page —
1-month came out cheaper per month than 3/6/12-month plans, since the trial
discount applied to the short option only.

The runtime grant path now creates rows with `max_months=None` so the
discount applies to every duration. This script aligns existing rows
(`source='trial_early_bird'`, `max_months=1`) with the new behavior.

Idempotent and safe to re-run. Dry-run by default; pass --apply to write.

Usage::

    PYTHONPATH=. python scripts/backfill_trial_early_bird_max_months.py --apply
    PYTHONPATH=. python scripts/backfill_trial_early_bird_max_months.py            # dry-run

"""

import argparse
import asyncio
import sys
from pathlib import Path

from tortoise import Tortoise

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bloobcat.clients import TORTOISE_ORM  # noqa: E402
from bloobcat.db.discounts import PersonalDiscount  # noqa: E402
from bloobcat.logger import get_logger  # noqa: E402
from bloobcat.services.trial_early_bird import (  # noqa: E402
    TRIAL_EARLY_BIRD_DISCOUNT_SOURCE,
)

logger = get_logger("scripts.backfill_trial_early_bird_max_months")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Clear stale max_months=1 on trial early-bird discounts so the "
            "−50% stimulus applies to every subscription duration."
        )
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="Persist changes. Without this flag the script runs in dry-run mode.",
    )
    return p.parse_args()


async def _run(apply: bool) -> int:
    qs = PersonalDiscount.filter(
        source=TRIAL_EARLY_BIRD_DISCOUNT_SOURCE,
        max_months=1,
    )
    rows = await qs.all()
    count = len(rows)
    for row in rows:
        logger.info(
            "Backfill candidate discount_id=%s user_id=%s percent=%s expires_at=%s",
            row.id,
            row.user_id,
            row.percent,
            row.expires_at.isoformat() if row.expires_at else None,
        )
    if apply and count > 0:
        await qs.update(max_months=None)
    return count


async def main() -> None:
    args = _parse_args()
    await Tortoise.init(config=TORTOISE_ORM)
    try:
        total = await _run(apply=args.apply)
    finally:
        await Tortoise.close_connections()
    if args.apply:
        logger.info("Backfill applied: %s trial early-bird rows updated.", total)
    else:
        logger.info(
            "Dry-run complete: %s rows would be updated. Re-run with --apply to persist.",
            total,
        )


if __name__ == "__main__":
    asyncio.run(main())
