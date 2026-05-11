"""Backfill `users.utm` so multi-hop referral chains inherit the campaign root.

Fills the gap for users registered before the downstream-attribution change:
when an existing user has no campaign utm of their own but was invited by
someone whose utm is a campaign tag (e.g. `qr_rt_launch_2026_05`), copy that
tag onto the invitee. Repeat until fixpoint so chains of arbitrary depth
converge.

Idempotent and safe to re-run. Dry-run by default; pass --apply to write.

Usage::

    PYTHONPATH=. python scripts/backfill_acquisition_utm.py --apply
    PYTHONPATH=. python scripts/backfill_acquisition_utm.py            # dry-run

"""

import argparse
import asyncio
import sys
from pathlib import Path

from tortoise import Tortoise
from tortoise.expressions import Q

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bloobcat.clients import TORTOISE_ORM  # noqa: E402
from bloobcat.db.users import Users  # noqa: E402
from bloobcat.funcs.referral_attribution import (  # noqa: E402
    is_campaign_utm,
    normalize_source_utm,
)
from bloobcat.logger import get_logger  # noqa: E402

logger = get_logger("scripts.backfill_acquisition_utm")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Backfill users.utm for downstream referral inheritance. "
            "Safe to re-run: idempotent and converges to a fixpoint."
        )
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="Persist changes. Without this flag the script runs in dry-run mode.",
    )
    p.add_argument(
        "--max-passes",
        type=int,
        default=10,
        help="Maximum passes over invitee chains. Default 10.",
    )
    p.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Batch size for invitee scans. Default 500.",
    )
    return p.parse_args()


async def _run(apply: bool, max_passes: int, batch_size: int) -> int:
    total_updated = 0
    for pass_idx in range(1, max_passes + 1):
        pass_updated = 0
        # Find candidate invitees: have a referrer, but their own utm is empty
        # or just the generic `partner` marker.
        candidates_qs = Users.filter(
            ~Q(referred_by__isnull=True),
            Q(utm__isnull=True) | Q(utm="") | Q(utm="partner"),
        )
        offset = 0
        while True:
            batch = await candidates_qs.offset(offset).limit(batch_size).all()
            if not batch:
                break
            for invitee in batch:
                referrer_id = int(getattr(invitee, "referred_by", 0) or 0)
                if not referrer_id:
                    continue
                # Self-loop guard: an invitee that points back at itself is a
                # data anomaly that the runtime _apply_* paths block but
                # backfill never validated. Skip rather than copy own utm.
                if referrer_id == int(invitee.id):
                    logger.warning(
                        "Backfill skipped self-referral user=%s", invitee.id
                    )
                    continue
                referrer = await Users.get_or_none(id=referrer_id)
                if not referrer:
                    continue
                # Two-node cycle guard: A referred_by B AND B referred_by A.
                # Historically possible — migration `83_…fix_users_referred_by_nullable_fk`
                # was added because of exactly this state. Without this guard
                # the utm tag pings back and forth between A and B forever
                # across max_passes iterations.
                if int(getattr(referrer, "referred_by", 0) or 0) == int(invitee.id):
                    logger.warning(
                        "Backfill skipped two-node cycle user=%s referrer=%s",
                        invitee.id,
                        referrer.id,
                    )
                    continue
                referrer_utm = normalize_source_utm(getattr(referrer, "utm", None))
                if not is_campaign_utm(referrer_utm):
                    continue
                current_utm = normalize_source_utm(getattr(invitee, "utm", None))
                if is_campaign_utm(current_utm):
                    continue
                logger.info(
                    "Backfill candidate user=%s referrer=%s old_utm=%r new_utm=%r",
                    invitee.id,
                    referrer.id,
                    current_utm or None,
                    referrer_utm,
                )
                if apply:
                    invitee.utm = referrer_utm
                    await invitee.save(update_fields=["utm"])
                pass_updated += 1
            offset += batch_size
        logger.info(
            "Pass %s: %s users %s",
            pass_idx,
            pass_updated,
            "updated" if apply else "would-be-updated (dry-run)",
        )
        total_updated += pass_updated
        if pass_updated == 0:
            break
    return total_updated


async def main() -> None:
    args = _parse_args()
    await Tortoise.init(config=TORTOISE_ORM)
    try:
        total = await _run(
            apply=args.apply,
            max_passes=args.max_passes,
            batch_size=args.batch_size,
        )
    finally:
        await Tortoise.close_connections()
    if args.apply:
        logger.info("Backfill applied: %s users updated.", total)
    else:
        logger.info(
            "Dry-run complete: %s users would be updated. Re-run with --apply to persist.",
            total,
        )


if __name__ == "__main__":
    asyncio.run(main())
