"""Periodic cleanup of stale push subscriptions.

Two passes per run:
  1. Deactivate rows that have failed delivery 8+ times in a row (delivery
     handler already does this on every send — this is a safety sweep for
     subscriptions that never received another delivery attempt).
  2. Delete rows that have been inactive for 60+ days. Once a browser rotates
     its endpoint or the user uninstalls, the row never comes back; keeping
     dead rows forever wastes storage and slows broadcasts.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from bloobcat.db.push_subscriptions import PushSubscription
from bloobcat.logger import get_logger

logger = get_logger("tasks.push_subscriptions_cleanup")

DEFAULT_INTERVAL_HOURS = 24
INACTIVE_DELETE_AFTER_DAYS = 60
FAILURE_DEACTIVATE_THRESHOLD = 8


async def cleanup_push_subscriptions() -> dict[str, int]:
    """Run a single cleanup pass. Returns counters for logging/observability."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=INACTIVE_DELETE_AFTER_DAYS)

    deactivated = await PushSubscription.filter(
        is_active=True,
        failure_count__gte=FAILURE_DEACTIVATE_THRESHOLD,
    ).update(is_active=False)

    # Delete inactive rows whose `updated_at` (last touched by the delivery
    # pipeline) is older than the cutoff. Active rows are never touched.
    deleted = await PushSubscription.filter(
        is_active=False,
        updated_at__lt=cutoff,
    ).delete()

    logger.info(
        "push_subscriptions cleanup: deactivated=%s deleted=%s",
        deactivated, deleted,
    )
    return {"deactivated": int(deactivated or 0), "deleted": int(deleted or 0)}


async def run_push_subscriptions_cleanup_scheduler(
    interval_hours: int = DEFAULT_INTERVAL_HOURS,
) -> None:
    """Long-running coroutine — call once from the bot lifespan startup."""
    interval_seconds = max(60, int(interval_hours) * 3600)
    logger.info("Starting push subscriptions cleanup scheduler (interval: %sh)", interval_hours)
    while True:
        try:
            await asyncio.sleep(interval_seconds)
            await cleanup_push_subscriptions()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Error in push subscriptions cleanup scheduler: %s", exc)
