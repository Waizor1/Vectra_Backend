"""Detect and optionally repair orphan home-screen reward claims.

A user is an "orphan claim" if their `Users.home_screen_reward_granted_at`
is set but they have no `PersonalDiscount(source='home_screen_install')`
row. This can happen when the pre-1.79.0 discount path crashed between
the timestamp UPDATE and the PersonalDiscount.create() call, locking the
user out of the bonus they "claimed" without delivering it.

Usage:
    # Dry-run report (no writes):
    python scripts/repair_home_screen_orphans.py --dry-run

    # Clear all orphan flags so users can retry from the app:
    python scripts/repair_home_screen_orphans.py --clear

    # Force-deliver the discount to every orphan (idempotent — no-ops on
    # users whose PersonalDiscount row already exists):
    python scripts/repair_home_screen_orphans.py --credit --kind=discount

    # Repair a single user (e.g. a support-ticket subject):
    python scripts/repair_home_screen_orphans.py --clear --user-id 2080225149

Safe to re-run. balance-kind orphans don't exist in the original code
(the UPDATE was an atomic single-row statement that set balance AND
flag together), so this script only reports/repairs discount-kind
orphans by default. To force-credit balance for a specific user use
`--credit --kind=balance --user-id <id>` — caller assumes responsibility
that the user wasn't already credited because the balance ledger has no
audit trail for the original grant.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from tortoise import Tortoise

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bloobcat.clients import TORTOISE_ORM  # noqa: E402
from bloobcat.logger import get_logger  # noqa: E402
from bloobcat.services.home_screen_rewards import (  # noqa: E402
    repair_home_screen_reward,
    scan_home_screen_orphans,
)

logger = get_logger("scripts.repair_home_screen_orphans")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Print orphan list and exit without making changes.",
    )
    mode.add_argument(
        "--clear",
        action="store_true",
        help="Drop home_screen_reward_granted_at for every orphan.",
    )
    mode.add_argument(
        "--credit",
        action="store_true",
        help="Deliver the missing reward for every orphan (idempotent).",
    )
    parser.add_argument(
        "--kind",
        choices=("balance", "discount"),
        default="discount",
        help="Required for --credit. balance forces +50 ₽; discount creates a PersonalDiscount row. (default: discount)",
    )
    parser.add_argument(
        "--user-id",
        type=int,
        default=None,
        help="Scope the scan to a single user id (e.g. 2080225149).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=2000,
        help="Max orphans to inspect per run (default 2000). Increase if the scan window is large.",
    )
    parser.add_argument(
        "--actor",
        type=str,
        default="repair-script",
        help="Free-text actor identifier written to the audit log. (default: repair-script)",
    )
    return parser.parse_args()


async def run() -> int:
    args = _parse_args()

    await Tortoise.init(config=TORTOISE_ORM)
    try:
        orphans = await scan_home_screen_orphans(
            user_id=args.user_id, limit=args.limit
        )
        if not orphans:
            print("No orphan home-screen claims found.")
            return 0

        print(f"Found {len(orphans)} orphan claim(s):")
        for row in orphans:
            print(
                f"  user_id={row['user_id']} granted_at={row['granted_at']} "
                f"balance={row['balance']} has_install_discount={row['has_install_discount']}"
            )

        if args.dry_run:
            return 0

        mode = "clear" if args.clear else "credit"
        kind = args.kind if mode == "credit" else None
        failures = 0
        for row in orphans:
            try:
                result = await repair_home_screen_reward(
                    user_id=int(row["user_id"]),
                    mode=mode,
                    reward_kind=kind,
                    actor=args.actor,
                )
                print(
                    f"  ✓ user_id={row['user_id']} → action={result['action']} repaired={result['repaired']}"
                )
            except Exception as exc:  # pragma: no cover - operational tool
                failures += 1
                print(f"  ✗ user_id={row['user_id']} failed: {exc}")
                logger.exception(
                    "repair failed for user_id=%s mode=%s kind=%s",
                    row["user_id"],
                    mode,
                    kind,
                )
        return 1 if failures else 0
    finally:
        await Tortoise.close_connections()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
