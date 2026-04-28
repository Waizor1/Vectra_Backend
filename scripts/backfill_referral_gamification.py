import argparse
import asyncio
import sys
from pathlib import Path

from tortoise import Tortoise

# Ensure the project root is importable when running as a script (so `import bloobcat` works).
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bloobcat.clients import TORTOISE_ORM  # noqa: E402
from bloobcat.db.referral_rewards import ReferralLevelRewards, ReferralRewards  # noqa: E402
from bloobcat.db.users import Users  # noqa: E402
from bloobcat.logger import get_logger  # noqa: E402
from bloobcat.services.referral_gamification import (  # noqa: E402
    REFERRAL_LEVELS,
    ensure_referral_level_rewards,
)

logger = get_logger("scripts.backfill_referral_gamification")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill ordinary referral level chests from existing first-payment ReferralRewards.\n"
            "Safe to re-run: it does not create retroactive cashback and chests are unique per level."
        )
    )
    parser.add_argument("--limit", type=int, default=5000, help="Max referrers to scan (default: 5000).")
    parser.add_argument("--apply", action="store_true", help="Actually create available chests (default: dry-run).")
    return parser.parse_args()


async def run() -> None:
    args = _parse_args()
    limit = max(1, int(args.limit or 1))
    apply_changes = bool(args.apply)

    await Tortoise.init(config=TORTOISE_ORM)
    try:
        referrer_ids = [
            int(row["referrer_user_id"])
            for row in await ReferralRewards.filter(kind="first_payment")
            .distinct()
            .values("referrer_user_id")
        ][:limit]
        scanned = 0
        would_create = 0
        created = 0
        skipped_partner = 0

        for referrer_id in referrer_ids:
            scanned += 1
            user = await Users.get_or_none(id=int(referrer_id))
            if not user:
                continue
            if bool(getattr(user, "is_partner", False)):
                skipped_partner += 1
                continue

            paid_count = await ReferralRewards.filter(
                referrer_user_id=int(referrer_id), kind="first_payment"
            ).count()
            reached_keys = [
                level.key
                for level in REFERRAL_LEVELS
                if level.key != "start" and paid_count >= level.threshold
            ]
            existing_keys = {
                str(row["level_key"])
                for row in await ReferralLevelRewards.filter(user_id=int(referrer_id)).values("level_key")
            }
            missing_keys = [key for key in reached_keys if key not in existing_keys]
            would_create += len(missing_keys)

            if not apply_changes:
                logger.info(
                    "[dry-run] user=%s paid_friends=%s missing_chests=%s",
                    referrer_id,
                    paid_count,
                    missing_keys,
                )
                continue

            created_rows = await ensure_referral_level_rewards(
                user_id=int(referrer_id), paid_friends_count=int(paid_count)
            )
            created += len(created_rows)
            if created_rows:
                logger.info(
                    "created referral chests user=%s levels=%s",
                    referrer_id,
                    [row.level_key for row in created_rows],
                )

        logger.info(
            "backfill complete scanned=%s would_create=%s created=%s skipped_partner=%s apply=%s",
            scanned,
            would_create,
            created,
            skipped_partner,
            apply_changes,
        )
    finally:
        await Tortoise.close_connections()


if __name__ == "__main__":
    asyncio.run(run())
