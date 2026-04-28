"""
Runtime self-heal for critical FK invariants verified by verify_runtime_state.py.
"""

import asyncio
import sys
from pathlib import Path

from tortoise import Tortoise

# Ensure project root imports work when launched as `python scripts/...`.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bloobcat.clients import TORTOISE_ORM
from bloobcat.db.fk_guards import (
    ensure_active_tariffs_fk_cascade,
    ensure_notification_marks_fk_cascade,
    ensure_promo_usages_fk_cascade,
    ensure_users_referred_by_fk_set_null,
)
from bloobcat.logger import get_logger

logger = get_logger("scripts.self_heal_runtime_state")


async def self_heal_runtime_state() -> None:
    await Tortoise.init(config=TORTOISE_ORM)
    try:
        results = [
            await ensure_active_tariffs_fk_cascade(),
            await ensure_notification_marks_fk_cascade(),
            await ensure_promo_usages_fk_cascade(),
            await ensure_users_referred_by_fk_set_null(),
        ]
    finally:
        await Tortoise.close_connections()

    if not all(results):
        raise RuntimeError(
            "Runtime FK self-heal incomplete: one or more guards returned False."
        )

    logger.info("Runtime FK self-heal completed successfully.")


def main() -> None:
    asyncio.run(self_heal_runtime_state())


if __name__ == "__main__":
    main()
