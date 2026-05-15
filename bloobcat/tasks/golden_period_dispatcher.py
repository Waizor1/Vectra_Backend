"""Hourly Golden Period dispatcher — activates eligible users.

Each hour we scan users that don't yet have any GoldenPeriod row, recompute
their cumulative active days (cached), and call `maybe_activate_golden_period`
for those that crossed the eligibility threshold. The chunk size + sleep
keeps the database from being hammered after the feature flag is flipped on
for the first time, when potentially thousands of long-time users may all
become eligible in the same pass.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from bloobcat.db.golden_period import GoldenPeriod
from bloobcat.db.users import Users
from bloobcat.logger import get_logger
from bloobcat.services.golden_period import (
    get_active_golden_period_config,
    maybe_activate_golden_period,
)

logger = get_logger("tasks.golden_period_dispatcher")
MOSCOW = ZoneInfo("Europe/Moscow")

CHUNK_SIZE = 500
INTER_CHUNK_PAUSE_SECONDS = 0.05


async def run_golden_period_dispatcher_once() -> int:
    """Single dispatcher pass. Returns the number of activations applied."""
    config = await get_active_golden_period_config()
    if not config.is_enabled:
        return 0

    activations = 0
    last_id = 0
    while True:
        # We want users that:
        #   * are registered + not partner + not blocked
        #   * have no GoldenPeriod row yet (one-shot lifetime)
        # Subquery via raw NOT EXISTS keeps the query plan flat on big users tables.
        chunk = (
            await Users.filter(
                id__gt=last_id,
                is_registered=True,
                is_partner=False,
                is_blocked=False,
            )
            .order_by("id")
            .limit(CHUNK_SIZE)
        )
        if not chunk:
            break
        # Filter out users that already have a GoldenPeriod row.
        ids = [int(u.id) for u in chunk]
        existing_rows = await GoldenPeriod.filter(user_id__in=ids).values_list(
            "user_id", flat=True
        )
        existing = {int(uid) for uid in existing_rows}
        candidates = [u for u in chunk if int(u.id) not in existing]

        for user in candidates:
            try:
                period = await maybe_activate_golden_period(user)
                if period is not None:
                    activations += 1
            except Exception:
                logger.exception(
                    "Golden Period activation tick failed for user=%s", user.id
                )

        last_id = int(chunk[-1].id)
        if len(chunk) < CHUNK_SIZE:
            break
        await asyncio.sleep(INTER_CHUNK_PAUSE_SECONDS)

    if activations:
        logger.info(
            "Golden Period dispatcher pass: %s activations", activations
        )
    return activations


def _next_hourly_run_time() -> datetime:
    now_msk = datetime.now(MOSCOW)
    target = datetime.combine(
        now_msk.date(), time(now_msk.hour, 0)
    ).replace(tzinfo=MOSCOW) + timedelta(hours=1)
    return target


async def run_golden_period_dispatcher_scheduler() -> None:
    """Long-running scheduler that runs hourly at :00 MSK."""
    logger.info("Golden Period dispatcher scheduler started")
    while True:
        next_run = _next_hourly_run_time()
        delay = (next_run - datetime.now(MOSCOW)).total_seconds()
        if delay > 0:
            await asyncio.sleep(delay)
        try:
            await run_golden_period_dispatcher_once()
        except Exception:
            logger.exception("Golden Period dispatcher scheduler tick failed")
