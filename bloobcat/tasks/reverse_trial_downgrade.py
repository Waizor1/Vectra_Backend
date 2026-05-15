"""Daily scheduler that downgrades reverse-trial accounts whose 7-day window
has elapsed.

Scheduled at 09:00 Moscow time. Processes states in chunks of 50 with a
short pause between chunks so a backlog after a feature flip does not
storm the database. Each downgrade is idempotent so a partial failure mid
chunk is safe to retry on the next pass.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from bloobcat.db.reverse_trial import ReverseTrialState
from bloobcat.logger import get_logger
from bloobcat.services.reverse_trial import downgrade_expired_reverse_trial

logger = get_logger("tasks.reverse_trial_downgrade")
MOSCOW = ZoneInfo("Europe/Moscow")

CHUNK_SIZE = 50
INTER_CHUNK_PAUSE_SECONDS = 0.1


async def run_reverse_trial_downgrade_once() -> int:
    """Process all expired active reverse-trial states. Returns the number of
    states processed (whether or not their downgrade succeeded).
    """
    now_utc = datetime.now(tz=MOSCOW).astimezone()
    cutoff = now_utc.utctimetuple()
    cutoff_dt = datetime(*cutoff[:6])

    processed = 0
    while True:
        chunk = await ReverseTrialState.filter(
            status="active", expires_at__lte=cutoff_dt
        ).limit(CHUNK_SIZE)
        if not chunk:
            break

        for state in chunk:
            try:
                await downgrade_expired_reverse_trial(state)
            except Exception:
                logger.exception(
                    "Reverse-trial downgrade failed for state=%s user=%s",
                    state.id,
                    state.user_id,
                )
            processed += 1

        if len(chunk) < CHUNK_SIZE:
            break
        await asyncio.sleep(INTER_CHUNK_PAUSE_SECONDS)

    if processed:
        logger.info("Reverse-trial downgrade pass processed %s states", processed)
    return processed


def _next_daily_run_time() -> datetime:
    now_msk = datetime.now(MOSCOW)
    target = datetime.combine(now_msk.date(), time(9, 0)).replace(tzinfo=MOSCOW)
    if target <= now_msk:
        target += timedelta(days=1)
    return target


async def run_reverse_trial_downgrade_scheduler() -> None:
    """Long-running scheduler that fires `run_reverse_trial_downgrade_once`
    once per day at 09:00 Moscow time.
    """
    logger.info("Reverse-trial downgrade scheduler started")
    while True:
        next_run = _next_daily_run_time()
        delay = (next_run - datetime.now(MOSCOW)).total_seconds()
        if delay > 0:
            await asyncio.sleep(delay)
        try:
            await run_reverse_trial_downgrade_once()
        except Exception:
            logger.exception("Reverse-trial downgrade scheduler tick failed")
