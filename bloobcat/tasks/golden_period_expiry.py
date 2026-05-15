"""10-minute Golden Period expiry scheduler.

Flips `status='active' AND expires_at <= NOW()` rows to `status='expired'`
in a single bulk UPDATE, then dispatches the `expired` notification for each
newly-expired row. Notification dispatch is best-effort — the period status
is already correct after the UPDATE, so a notification failure does not need
a retry.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from bloobcat.db.golden_period import GoldenPeriod
from bloobcat.db.users import Users
from bloobcat.logger import get_logger

logger = get_logger("tasks.golden_period_expiry")
MOSCOW = ZoneInfo("Europe/Moscow")

TICK_INTERVAL_SECONDS = 600  # 10 minutes


async def run_golden_period_expiry_once() -> int:
    """Single expiry pass. Returns number of periods flipped to expired."""
    now_utc = datetime.now(timezone.utc)
    candidates = await GoldenPeriod.filter(
        status="active", expires_at__lte=now_utc
    )
    if not candidates:
        return 0

    expired_count = 0
    for period in candidates:
        # Re-fetch + atomic flip to avoid races with the dispatcher.
        updated = await GoldenPeriod.filter(
            id=int(period.id), status="active"
        ).update(status="expired")
        if not updated:
            continue
        expired_count += 1

        # Refresh + dispatch the expiry notification (best-effort).
        try:
            user = await Users.get_or_none(id=int(period.user_id))
            if user is None:
                continue
            from bloobcat.bot.notifications.golden_period.expired import (
                notify_golden_period_expired,
            )

            refreshed = await GoldenPeriod.get(id=int(period.id))
            await notify_golden_period_expired(user, refreshed)
        except Exception:
            logger.exception(
                "Golden Period expiry notification failed period=%s", period.id
            )

    if expired_count:
        logger.info("Golden Period expiry pass: %s expired", expired_count)
    return expired_count


async def run_golden_period_expiry_scheduler() -> None:
    """Long-running scheduler that runs every 10 minutes."""
    logger.info("Golden Period expiry scheduler started")
    while True:
        try:
            await run_golden_period_expiry_once()
        except Exception:
            logger.exception("Golden Period expiry scheduler tick failed")
        await asyncio.sleep(TICK_INTERVAL_SECONDS)
